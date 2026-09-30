// Copyright 2026 Intrinsic Innovation LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef GRIPPER_CMD_SKILL_H
#define GRIPPER_CMD_SKILL_H

#include <memory>
#include <optional>
#include <string>

#include "absl/status/statusor.h"
#include "absl/time/time.h"
#include "gripper_cmd_skill.pb.h"
#include "intrinsic/skills/cc/skill_interface.h"

// Commands a parallel gripper, e.g. the Robotiq Hand-E.
//
// Sends the parameters as a Zenoh query to the gripper service's gateway, which
// runs them as a control_msgs/action/ParallelGripperCommand goal and replies
// with the result.
class GripperCmdSkill final : public intrinsic::skills::SkillInterface {
 public:
  static std::unique_ptr<intrinsic::skills::SkillInterface> CreateSkill();

  absl::StatusOr<std::unique_ptr<google::protobuf::Message>> Execute(
      const intrinsic::skills::ExecuteRequest& request,
      intrinsic::skills::ExecuteContext& context) override;
};

namespace gripper_cmd_skill {

// Zenoh key of the gateway's queryable.
inline constexpr char kQueryKey[] = "gripper_cmd";

// Sends `params` to the gateway and interprets its reply. Without `timeout`,
// the Zenoh session's default query timeout applies.
absl::StatusOr<ai::intrinsic::GripperCmdResult> CallGateway(
    const ai::intrinsic::GripperCmdParams& params,
    std::optional<absl::Duration> timeout);

// The Zenoh endpoints to connect to: those in ZENOH_CONFIG_OVERRIDE's
// connect/endpoints, else the cluster's zenoh router, else localhost.
std::string ConnectEndpoints();

}  // namespace gripper_cmd_skill

#endif  // GRIPPER_CMD_SKILL_H
