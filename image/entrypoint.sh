#!/bin/bash
set -e

source /opt/ros/lyrical/setup.bash
source /workspace/install/setup.bash

exec /usr/bin/python3 /workspace/entrypoint.py "$@"
