#!/bin/sh
echo "kh-monitor starting — interval: ${COLLECT_INTERVAL:-60}s"

while true; do
    python3 monitor.py measure
    sleep ${COLLECT_INTERVAL:-60}
done