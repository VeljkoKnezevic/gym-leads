import os, re, subprocess, sys
from pathlib import Path
import streamlit as st

# Install Chromium at startup (cached after first run on Streamlit Cloud)
if not os.environ.get("PLAYWRIGHT_INSTALLED"):
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                   capture_output=True)
    os.environ["PLAYWRIGHT_INSTALLED"] = "1"

st.set_page_config(page_title="Gym Lead Scraper", layout="centered")
st.title("Gym Lead Scraper")

city = st.text_input("City", placeholder='e.g. "Fort Wayne, IN"')
sources = st.multiselect(
    "Sources",
    ["mindbody", "crossfit", "google_maps", "hyrox"],
    default=["mindbody", "crossfit", "google_maps", "hyrox"],
)

if st.button("Run Scraper", disabled=not city.strip() or not sources):
    slug = re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-")
    output_path = Path("output") / f"{slug}-leads.csv"

    with st.spinner(f"Scraping {city.strip()} — ~2 min on cloud..."):
        result = subprocess.run(
            [sys.executable, "scrape.py",
             "--city", city.strip(),
             "--sources", *sources,
             "--sequential"],        # keep memory < 512 MB
            capture_output=True, text=True, cwd=Path(__file__).parent,
        )

    st.code(result.stdout or result.stderr)

    if result.returncode == 0 and output_path.exists():
        st.success("Done!")
        st.download_button(
            "Download CSV",
            output_path.read_bytes(),
            file_name=output_path.name,
            mime="text/csv",
        )
    else:
        st.error("Scraper failed — see log above")
