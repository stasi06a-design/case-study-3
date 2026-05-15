#!/bin/sh
echo "kh-monitor starting — interval: ${COLLECT_INTERVAL:-60}s"

while true; do
    python3 monitor.py measure
    python3 container_monitor.py
    sleep ${COLLECT_INTERVAL:-60}
done