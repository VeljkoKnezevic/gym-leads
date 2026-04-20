"""Fetch Google reviews for gym leads, then summarize with Ollama.

Hybrid source strategy (cheapest first):
  - Leads with a CID -> Serper /reviews   (1 credit each, ~$0.005)
  - Leads without CID -> single Apify compass~crawler-google-places call
    (returns CID + reviews together, skipping a second actor run)

Results cached to .cache/reviews/<slug>.json so a mid-run Ollama crash
does NOT force us to re-pay the data providers on retry.

Usage:
    python reviews.py --input output/altamonte-springs-fl.csv
    python reviews.py --input output/altamonte-springs-fl.csv --workers 4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scrapers.base import Lead
from utils.csv_writer import read_leads_csv, write_leads_csv

# Load .env if present
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

APIFY_PLACES_ACTOR = "compass~crawler-google-places"
APIFY_BASE = "https://api.apify.com/v2"
SERPER_REVIEWS_URL = "https://google.serper.dev/reviews"

_SNIPPET_PROMPT = """\
You are writing a short review callout for a cold email to a gym owner.

Pick ONE specific thing the reviewer praised and phrase it so it fits mid-sentence \
in this email template:

"Noticed on Google the review [reviewer_name] left about [YOUR OUTPUT HERE]."

## Rules
1. Output ONLY the phrase — no quotes, no period, no prefix.
2. KEEP IT SHORT — aim for 6-10 words, never more than 12.
3. Compliment ONE specific thing (coach name, class type, atmosphere, result).
4. The phrase must sound natural right after "about". Good openings:
     "how much [he/she] loves your ..."
     "how [adjective] your ... is"
     "what [an adjective] ... [Coach Name] is"
     "the [adjective] ... from your ..."
5. Use "your" to address the gym owner. Third-person pronouns (he/she/they) for reviewer.
6. Start lowercase (it continues the sentence).

## Examples
Review (James): "Coach Sarah is amazing! Her HIIT classes push me to my limits every time."
Output: how much he loves Coach Sarah's HIIT classes

Review (Emma): "Best spin studio in town. The morning classes are incredible and the energy is unmatched."
Output: how much she loves your morning spin classes

Review (Michael): "I've been coming here for 2 years. The community feeling and personal attention from the trainers is what keeps me coming back."
Output: how welcoming your community is

Review (Linda): "Ty and Jo Anna Pope are exceptional coaches with over 30 years of experience."
Output: what exceptional coaches Ty and Jo Anna are

Review (Daniel): "The 6am bootcamp with Jake gets me fired up every single day."
Output: how much he loves Jake's 6am bootcamp

Review (Priya): "Lost 30 lbs in 6 months thanks to the personal training here."
Output: the life-changing results from your personal training

## Now summarize this review:
Reviewer: {reviewer_name}
Review: {review_text}
Output:"""


def _get_apify_token() -> str:
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        print("[reviews] APIFY_API_TOKEN not set — crawler fallback disabled")
    return token


def _get_serper_key() -> str:
    key = os.environ.get("SERPER_API_KEY", "")
    if not key:
        print("[reviews] SERPER_API_KEY not set — CID-based fetch disabled")
    return key


def _has_valid_cid(lead: Lead) -> bool:
    return bool(lead.google_cid) and lead.google_cid.isdigit() and len(lead.google_cid) >= 10


def _run_apify_actor(actor: str, actor_input: dict, token: str, label: str = "apify") -> list[dict]:
    """Run an Apify actor synchronously, poll until done, return dataset items."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.post(
            f"{APIFY_BASE}/acts/{actor}/runs",
            json=actor_input,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        run_data = resp.json().get("data", {})
        run_id = run_data.get("id")
        if not run_id:
            print(f"[{label}] Failed to start actor: {resp.text}")
            return []
    except Exception as e:
        print(f"[{label}] Failed to start actor: {e}")
        return []

    print(f"[{label}] Actor run {run_id} started — waiting for completion...")
    poll_url = f"{APIFY_BASE}/actor-runs/{run_id}"
    start_time = time.time()
    status = "UNKNOWN"
    while True:
        try:
            resp = requests.get(poll_url, headers=headers, timeout=15)
            status = resp.json().get("data", {}).get("status", "")
        except Exception:
            status = "UNKNOWN"

        elapsed = time.time() - start_time
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            print(f"[{label}] Actor run {status} after {elapsed:.0f}s")
            break
        if elapsed > 600:
            print(f"[{label}] Actor run timed out after {elapsed:.0f}s")
            return []
        time.sleep(10)

    if status != "SUCCEEDED":
        return []

    dataset_id = run_data.get("defaultDatasetId")
    if not dataset_id:
        print(f"[{label}] No dataset ID in run response")
        return []

    try:
        resp = requests.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items?format=json",
            headers=headers,
            timeout=60,
        )
        return resp.json()
    except Exception as e:
        print(f"[{label}] Failed to fetch dataset: {e}")
        return []


def _fetch_reviews_serper(leads_with_cid: list[Lead], api_key: str,
                           workers: int = 8) -> dict[str, list[dict]]:
    """Fetch reviews for each CID-having lead via Serper /reviews (parallel).

    1 credit per call, ~10 reviews returned per call. Returns dict[cid -> reviews].
    """
    if not leads_with_cid or not api_key:
        return {}

    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    results: dict[str, list[dict]] = {}

    def fetch_one(lead: Lead) -> tuple[str, list[dict]]:
        try:
            resp = requests.post(
                SERPER_REVIEWS_URL,
                json={"cid": lead.google_cid},
                headers=headers,
                timeout=30,
            )
            if resp.status_code != 200:
                return lead.google_cid, []
            return lead.google_cid, resp.json().get("reviews", []) or []
        except Exception:
            return lead.google_cid, []

    print(f"[reviews] Fetching reviews via Serper for {len(leads_with_cid)} leads "
          f"with CIDs ({workers} workers)...")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch_one, l) for l in leads_with_cid]
        for i, fut in enumerate(as_completed(futures), 1):
            cid, revs = fut.result()
            if revs:
                results[cid] = revs

    print(f"[reviews] Serper returned reviews for {len(results)}/{len(leads_with_cid)} CIDs")
    return results


def _fetch_reviews_apify_crawler(leads_without_cid: list[Lead], token: str,
                                   max_reviews: int = 3
                                   ) -> tuple[dict[str, list[dict]], dict[str, list[dict]], int]:
    """Single Apify crawler call for leads missing CIDs — returns CIDs AND reviews in one run.

    Mutates leads_without_cid in place to set google_cid when resolved.
    Returns (by_title, by_cid, cids_backfilled).
    """
    if not leads_without_cid or not token:
        return {}, {}, 0

    search_strings = []
    for lead in leads_without_cid:
        parts = [lead.name, lead.address, lead.city, lead.state]
        search_strings.append(" ".join(p for p in parts if p).strip())

    actor_input = {
        "searchStringsArray": search_strings,
        "maxCrawledPlacesPerSearch": 1,
        "maxReviews": max_reviews,
        "language": "en",
        "scrapePlaceDetailPage": False,
        "skipClosedPlaces": False,
    }

    print(f"[reviews] Apify crawler: resolving CID + reviews (maxReviews={max_reviews}) "
          f"for {len(leads_without_cid)} leads...")
    items = _run_apify_actor(APIFY_PLACES_ACTOR, actor_input, token, label="apify-crawler")
    if not items:
        return {}, {}, 0

    by_title_raw: dict[str, dict] = {}
    by_title: dict[str, list[dict]] = {}
    by_cid: dict[str, list[dict]] = {}
    for item in items:
        title = (item.get("title") or "").strip().lower()
        cid = str(item.get("cid") or "").strip()
        revs = item.get("reviews", []) or []
        if title:
            by_title_raw.setdefault(title, item)
            if revs:
                by_title[title] = revs
        if cid and cid.isdigit() and revs:
            by_cid[cid] = revs

    # Backfill CIDs on the leads themselves by fuzzy-matching titles back to input leads.
    added = 0
    for lead in leads_without_cid:
        lead_name = lead.name.strip().lower()
        match = by_title_raw.get(lead_name)
        if not match:
            for t, item in by_title_raw.items():
                if lead_name in t or t in lead_name:
                    match = item
                    break
        if not match:
            lead_words = set(re.sub(r"[^a-z0-9\s]", "", lead_name).split())
            lead_words -= {"the", "a", "an", "of", "in", "at", "and", "or", "llc", "inc"}
            lead_street = lead.address.split(",")[0].strip().lower() if lead.address else ""
            for t, item in by_title_raw.items():
                t_words = set(re.sub(r"[^a-z0-9\s]", "", t).split())
                t_words -= {"the", "a", "an", "of", "in", "at", "and", "or", "llc", "inc"}
                overlap = lead_words & t_words
                addr_match = lead_street and lead_street in (item.get("address") or "").lower()
                if (len(overlap) >= 2 or addr_match) and overlap:
                    match = item
                    break
        if match:
            cid = str(match.get("cid") or "").strip()
            if cid and cid.isdigit():
                lead.google_cid = cid
                added += 1

    print(f"[reviews] Crawler returned {len(by_title)} places with reviews "
          f"({added} CIDs backfilled)")
    return by_title, by_cid, added


def _pick_best_reviews(reviews: list[dict], top_n: int = 3) -> list[tuple[str, str]]:
    """Pick the best positive reviews for LLM summarization.

    Returns list of (first_name, review_text) tuples, up to top_n.
    """
    candidates = []

    for r in reviews:
        rating = r.get("stars") or r.get("rating", 0)
        text = r.get("reviewText") or r.get("text") or r.get("snippet", "")
        name = r.get("name") or r.get("reviewerName", "")
        # Handle nested user dict (Serper format)
        if not name:
            user = r.get("user", {})
            if isinstance(user, dict):
                name = user.get("name", "")
            elif user:
                name = str(user)

        if not isinstance(rating, (int, float)):
            try:
                rating = int(rating)
            except (ValueError, TypeError):
                continue

        if rating < 4 or not text or len(text) < 20:
            continue
        if not name or len(name.strip()) < 2:
            continue

        # Score by specificity
        text_lower = text.lower()
        score = 0
        if rating == 5:
            score += 50

        specifics = ["coach", "trainer", "instructor", "class", "session",
                     "workout", "morning", "evening", "program", "community",
                     "atmosphere", "staff", "helped", "results", "lost weight",
                     "stronger", "transformed"]
        score += sum(30 for s in specifics if s in text_lower)

        # Bonus for mentioning a proper name
        name_mentions = re.findall(r'\b[A-Z][a-z]+\b', text)
        skip = {"The", "This", "That", "They", "Their", "There", "What", "When",
                "Where", "Great", "Best", "Amazing", "Love", "Would", "Could",
                "Very", "Really", "Every", "After", "Before", "From", "Been",
                "Just", "Also", "Here", "Will", "Always", "Never", "Highly"}
        real_names = [n for n in name_mentions if n not in skip]
        if real_names:
            score += 50

        if len(text) > 500:
            score -= 20

        first_name = name.strip().split()[0]
        candidates.append((score, first_name, text))

    if not candidates:
        return []

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [(name, text) for _, name, text in candidates[:top_n]]


def _summarize_with_ollama(reviewer_name: str, review_text: str,
                            model: str, host: str) -> str:
    """Use Ollama to generate a short, email-ready review snippet."""
    if len(review_text) > 800:
        review_text = review_text[:800] + "..."

    prompt = _SNIPPET_PROMPT.format(
        reviewer_name=reviewer_name,
        review_text=review_text,
    )

    try:
        resp = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "think": False},
            timeout=60,
        )
        if resp.status_code != 200:
            return ""
        raw = resp.json().get("response", "").strip()
    except Exception:
        return ""

    snippet = raw.strip().strip('"').strip("'").strip(".")
    for prefix in ("Output:", "output:", "Summary:", "summary:"):
        if snippet.lower().startswith(prefix.lower()):
            snippet = snippet[len(prefix):].strip()

    if snippet and snippet[0].isupper():
        if len(snippet) > 1 and not snippet[1].isupper():
            snippet = snippet[0].lower() + snippet[1:]

    if len(snippet) > 90:
        snippet = snippet[:87].rsplit(" ", 1)[0] + "..."

    return snippet


def _match_lead_to_reviews(lead: Lead, reviews_by_title: dict[str, list[dict]],
                            reviews_by_cid: dict[str, list[dict]]) -> list[dict]:
    """Find reviews matching a lead — prefer CID exact match, fall back to fuzzy title."""
    if lead.google_cid and lead.google_cid in reviews_by_cid:
        return reviews_by_cid[lead.google_cid]

    lead_name = lead.name.strip().lower()

    if lead_name in reviews_by_title:
        return reviews_by_title[lead_name]

    for place_name, reviews in reviews_by_title.items():
        if lead_name in place_name or place_name in lead_name:
            return reviews

    lead_words = set(re.sub(r"[^a-z0-9\s]", "", lead_name).split())
    lead_words -= {"the", "a", "an", "of", "in", "at", "and", "or", "llc", "inc"}
    if not lead_words:
        return []

    for place_name, reviews in reviews_by_title.items():
        place_words = set(re.sub(r"[^a-z0-9\s]", "", place_name).split())
        place_words -= {"the", "a", "an", "of", "in", "at", "and", "or", "llc", "inc"}
        overlap = lead_words & place_words
        if len(overlap) >= 2 or (len(lead_words) == 1 and overlap):
            return reviews

    return []


def _process_lead_with_reviews(lead: Lead, reviews: list[dict],
                                model: str, host: str) -> tuple[Lead, str]:
    """Process a single lead given its pre-fetched reviews."""
    if lead.review_name and lead.review_snippet:
        return lead, "already_done"

    if not reviews:
        return lead, "no_reviews"

    candidates = _pick_best_reviews(reviews, top_n=3)
    if not candidates:
        return lead, "no_good_review"

    for first_name, review_text in candidates:
        snippet = _summarize_with_ollama(first_name, review_text, model, host)
        if snippet and len(snippet) > 5:
            lead.review_name = first_name
            lead.review_snippet = snippet
            return lead, "found"

    return lead, "summarize_failed"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Google reviews via Serper (+ Apify fallback) and summarize with Ollama")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="", help="Output CSV path (default: overwrite input)")
    parser.add_argument("--workers", type=int, default=2,
                        help="Parallel workers for LLM summarization (default: 2)")
    parser.add_argument("--fetch-workers", type=int, default=8,
                        help="Parallel workers for Serper /reviews (default: 8)")
    parser.add_argument("--model", default="qwen3:14b",
                        help="Ollama model for review summarization (default: qwen3:14b)")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    parser.add_argument("--max-reviews", type=int, default=3,
                        help="Max reviews to fetch per place via Apify crawler (default: 3)")
    args = parser.parse_args()

    apify_token = _get_apify_token()
    serper_key = _get_serper_key()
    if not apify_token and not serper_key:
        print("[reviews] Both SERPER_API_KEY and APIFY_API_TOKEN are missing — cannot fetch")
        sys.exit(1)

    output_path = args.output or args.input
    leads = read_leads_csv(args.input)

    to_process = [l for l in leads if not (l.review_name and l.review_snippet)]
    already_done = len(leads) - len(to_process)

    print(f"[reviews] {len(leads)} total leads — {len(to_process)} to fetch, {already_done} already done")
    print(f"[reviews] Model: {args.model}")

    if not to_process:
        print("[reviews] Nothing to do.")
        return

    have_cid = [l for l in to_process if _has_valid_cid(l)]
    no_cid = [l for l in to_process if not _has_valid_cid(l)]
    print(f"[reviews] Routing: {len(have_cid)} via Serper /reviews, "
          f"{len(no_cid)} via Apify crawler")

    # Cache Serper + crawler results so an Ollama crash during summarization
    # does NOT force re-paying the data providers.
    cache_path = Path(".cache/reviews") / (Path(args.input).stem + ".json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    reviews_by_title: dict[str, list[dict]] = {}
    reviews_by_cid: dict[str, list[dict]] = {}

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            reviews_by_title = cached.get("by_title", {})
            reviews_by_cid = cached.get("by_cid", {})
            print(f"[reviews] Loaded cache from {cache_path} "
                  f"(cid={len(reviews_by_cid)}, title={len(reviews_by_title)})")
        except Exception as e:
            print(f"[reviews] Failed to read cache {cache_path}: {e}")

    if not reviews_by_title and not reviews_by_cid:
        # Serper pass (cheap, for leads with CIDs)
        if have_cid and serper_key:
            serper_by_cid = _fetch_reviews_serper(have_cid, serper_key, args.fetch_workers)
            reviews_by_cid.update(serper_by_cid)

        # Apify crawler pass (resolves CIDs AND fetches reviews for the rest)
        if no_cid and apify_token:
            crawler_by_title, crawler_by_cid, added = _fetch_reviews_apify_crawler(
                no_cid, apify_token, args.max_reviews
            )
            reviews_by_title.update(crawler_by_title)
            reviews_by_cid.update(crawler_by_cid)
            if added:
                write_leads_csv(leads, output_path)
                print(f"[reviews] Saved {added} newly-backfilled CIDs to CSV")

        if reviews_by_title or reviews_by_cid:
            cache_path.write_text(
                json.dumps({"by_title": reviews_by_title, "by_cid": reviews_by_cid}),
                encoding="utf-8",
            )
            print(f"[reviews] Cached results to {cache_path}")

    if not reviews_by_title and not reviews_by_cid:
        print("[reviews] No reviews fetched — check SERPER_API_KEY / APIFY_API_TOKEN")
        write_leads_csv(leads, output_path)
        return

    # Step 3: Match reviews to leads and summarize with LLM
    stats: dict[str, int] = {}
    matched = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for lead in to_process:
            reviews = _match_lead_to_reviews(lead, reviews_by_title, reviews_by_cid)
            if reviews:
                matched += 1
            futures[executor.submit(
                _process_lead_with_reviews, lead, reviews, args.model, args.host
            )] = lead

        for i, future in enumerate(as_completed(futures), 1):
            orig_lead = futures[future]
            try:
                updated_lead, status = future.result()
                stats[status] = stats.get(status, 0) + 1
                if status == "found":
                    print(f"[reviews] [{i}/{len(to_process)}] {updated_lead.name!r} -> "
                          f"{updated_lead.review_name}: \"{updated_lead.review_snippet}\"")
                else:
                    print(f"[reviews] [{i}/{len(to_process)}] {updated_lead.name!r} -> {status}")
            except Exception as e:
                print(f"[reviews] [{i}/{len(to_process)}] ERROR on '{orig_lead.name}': {e}")
                stats["error"] = stats.get("error", 0) + 1

    write_leads_csv(leads, output_path)

    total_with_reviews = sum(1 for l in leads if l.review_name)
    print(f"\n[reviews] === Summary ===")
    print(f"[reviews] Reviews matched: {matched}/{len(to_process)}")
    for k, v in sorted(stats.items()):
        print(f"[reviews]   {k}: {v}")
    print(f"[reviews] Total leads with reviews: {total_with_reviews}/{len(leads)} "
          f"({100*total_with_reviews//len(leads)}%)" if leads else "")
    print(f"[reviews] Output: {output_path}")


if __name__ == "__main__":
    main()
