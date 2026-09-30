"""Message classes built from the descriptor set shipped in the skill bundle.

The harness does not depend on generated code: every message it needs
(intrinsic skill service, gripper_cmd_skill params/result) is in the transitive
descriptor set that the original bundle carries.
"""

import os

from google.protobuf import descriptor_pb2
from google.protobuf import descriptor_pool
from google.protobuf import message_factory

_DIR = os.path.dirname(os.path.abspath(__file__))

_pool = descriptor_pool.DescriptorPool()
_fds = descriptor_pb2.FileDescriptorSet.FromString(
    open(os.path.join(_DIR, "skill_descriptors.bin"), "rb").read())
_added = set()
_by_name = {f.name: f for f in _fds.file}


def _add(name):
  if name in _added:
    return
  f = _by_name[name]
  for dep in f.dependency:
    _add(dep)
  _pool.Add(f)
  _added.add(name)


for _f in _fds.file:
  _add(_f.name)


def msg(full_name):
  """Returns the message class for a fully qualified proto name."""
  return message_factory.GetMessageClass(
      _pool.FindMessageTypeByName(full_name))
