"""Builds the test fixtures for capture_simdriver.py (hex on stdout)."""
import sys, time
from google.protobuf.internal import encoder
import protos

def status_packet(position, reached=True, object_detected=False):
  st = protos.msg("intrinsic_proto.gripper.PinchGripperStatus")(
      position=position, position_reached=reached, gripper_enabled=True,
      in_motion=False, object_detected=object_detected)
  pkt = protos.msg("intrinsic_proto.pubsub.PubSubPacket")()
  pkt.payload.Pack(st)
  pkt.publish_time.seconds = 1790000000
  body = pkt.SerializeToString()
  return encoder._VarintBytes(len(body)) + body

def gripper_object():
  o = protos.msg("intrinsic_proto.world.Object")(
      world_id="world", id="gripper_id", name="gripper", type=3)
  o.kinematic_object_component.joint_positions.extend([0.0])
  o.kinematic_object_component.joint_entity_ids.extend(["finger_joint_id"])
  return o.SerializeToString()

what = sys.argv[1]
if what == "status":
  print(status_packet(float(sys.argv[2]), object_detected=len(sys.argv) > 3).hex())
elif what == "object":
  print(gripper_object().hex())
