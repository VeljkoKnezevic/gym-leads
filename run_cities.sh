#!/usr/bin/env bash
# Run pipeline for each city sequentially, no enrichment.
# Log: output/run_cities.log

LOG="output/run_cities.log"
mkdir -p output

CITIES=(
  "Boca Raton, FL"
  "Bonita Springs, FL"
  "Boynton Beach, FL"
  "Bradenton, FL"
  "Brandon, FL"
  "Cape Coral, FL"
  "Clearwater, FL"
  "Clermont, FL"
  "Coral Springs, FL"
  "Davie, FL"
  "Delray Beach, FL"
  "Doral, FL"
  "Fort Myers, FL"
  "Jacksonville Beach, FL"
  "Jupiter, FL"
  "Lake Mary, FL"
)

TOTAL=${#CITIES[@]}
PASSED=0
FAILED=0

log() { echo "$@" | tee -a "$LOG"; }

log "========================================"
log "  START: $(date)"
log "  Cities: $TOTAL"
log "========================================"

for i in "${!CITIES[@]}"; do
  CITY="${CITIES[$i]}"
  NUM=$((i + 1))
  log ""
  log "[$NUM/$TOTAL] Starting: $CITY  ($(date +%H:%M:%S))"

  python pipeline.py --city "$CITY" >> "$LOG" 2>&1
  RC=$?

  if [ $RC -eq 0 ]; then
    PASSED=$((PASSED + 1))
    log "[$NUM/$TOTAL] DONE: $CITY"
  else
    FAILED=$((FAILED + 1))
    log "[$NUM/$TOTAL] FAILED (exit $RC): $CITY"
  fi
done

log ""
log "========================================"
log "  FINISHED: $(date)"
log "  Passed: $PASSED / $TOTAL"
log "  Failed: $FAILED / $TOTAL"
log "========================================"
