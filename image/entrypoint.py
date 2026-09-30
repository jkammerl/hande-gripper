#!/usr/bin/python3
import os
import sys
import subprocess

config_file = '/etc/intrinsic/runtime_config.pb'

if "ZENOH_CONFIG_OVERRIDE" not in os.environ:
    if os.path.exists(config_file):
        os.environ["ZENOH_CONFIG_OVERRIDE"] = 'connect/endpoints=["tcp/zenoh-router.app-intrinsic-base.svc.cluster.local:7447"]'
    else:
        os.environ["ZENOH_CONFIG_OVERRIDE"] = 'connect/endpoints=["tcp/127.0.0.1:7447"]'
print(f"ENTRYPOINT: ZENOH_CONFIG_OVERRIDE set to: {os.environ.get('ZENOH_CONFIG_OVERRIDE')}", flush=True)

# Add config_protos to path
sys.path.append('/workspace/config_protos')

try:
    import runtime_context_pb2
    import hande_gripper_aquarium_hande_gripper_launch_xml_config_pb2 as service_config_pb2
except ImportError as e:
    print(f"ENTRYPOINT ERROR: Failed to import proto modules: {e}", flush=True)
    # Fallback to default launch without overrides
    subprocess.run(["ros2", "launch", "/workspace/aquarium_launch.xml"])
    sys.exit(0)

launch_args = []
allowed_args = []

if os.path.exists(config_file):
    try:
        with open(config_file, 'rb') as f:
            context = runtime_context_pb2.RuntimeContext.FromString(f.read())
        
        if context.config.Is(service_config_pb2.HandeGripperAquariumHandeGripperLaunchXmlConfig.DESCRIPTOR):
            config = service_config_pb2.HandeGripperAquariumHandeGripperLaunchXmlConfig()
            context.config.Unpack(config)
            
            for field in config.DESCRIPTOR.fields:
                if field.name in allowed_args:
                    val = getattr(config, field.name)
                    if field.label != field.LABEL_REPEATED:
                        launch_args.append(f"{field.name}:={val}")
            print(f"ENTRYPOINT: Found runtime config overrides: {launch_args}", flush=True)
    except Exception as e:
        print(f"ENTRYPOINT WARNING: Failed to parse runtime config: {e}", flush=True)

cmd = ["ros2", "launch", "/workspace/aquarium_launch.xml"] + launch_args
print(f"ENTRYPOINT: Executing: {' '.join(cmd)}", flush=True)
os.execvp(cmd[0], cmd)
