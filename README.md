# Robotiq Hand-E gripper for Intrinsic OMTS, built natively

The `hand_e_gripper_service` and `hand_e_gripper_cmd_skill` asset bundles that
the [Open Machine Tending Solution](https://github.com/intrinsic-ai/intrinsic-omts)
uses contain amd64-only binaries without public sources. This repository
replaces them with sources that build for any CPU, with the same interfaces,
as part of running Intrinsic Flowstate and OMTS on arm64; start at
[jkammerl/native-images](https://github.com/jkammerl/native-images).

| Directory | Replaces |
| :--- | :--- |
| `gripper_cmd_skill/` | The `gripper_cmd_skill` binary: a C++ skill with the original manifest and messages. |
| `aquarium_gateway/` | The gateway that serves the gripper's ROS 2 action to skills over Zenoh and gRPC, as configured in `aquarium.yaml`. |
| `hande_gripper_sim_driver/` | The simulation driver: a `ParallelGripperCommand` action server that drives the simulated gripper over Intrinsic pubsub and the world service. |
| `hande_gripper_aquarium/`, `image/` | The original images' entrypoints, launch files and configuration; the real driver is [AGH-CEAI/robotiq_hande_driver](https://github.com/AGH-CEAI/robotiq_hande_driver) v0.2.0-jazzy, patched for ROS 2 Lyrical. |
| `oracle/` | The harness that recorded the original images' behavior (run under emulation) and compares the replacements with it: all recorded scenarios match. |

Build the images (then put them into the bundles with
`make_omts_bundles.sh` from native-images):

```bash
podman build -f image/service.Containerfile --build-arg VARIANT=sim -t localhost/hande-gripper-service:sim .
podman build -f image/service.Containerfile --build-arg VARIANT=real -t localhost/hande-gripper-service:real .
podman build -f image/skill.Containerfile -t localhost/gripper-cmd-skill:arm64 .
```

Licensed under the Apache License 2.0.
