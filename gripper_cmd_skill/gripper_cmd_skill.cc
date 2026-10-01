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

#include "gripper_cmd_skill.h"

#include <cstdlib>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <variant>
#include <vector>

#include "absl/log/log.h"
#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/numbers.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/str_split.h"
#include "absl/strings/string_view.h"
#include "absl/time/clock.h"
#include "absl/time/time.h"
#include "gripper_cmd_skill.pb.h"
#include "intrinsic/skills/cc/skill_utils.h"
#include "intrinsic/skills/proto/skill_service.pb.h"
#include "intrinsic/util/status/status_macros.h"
#include "zenoh.hxx"

using ::ai::intrinsic::GripperCmdParams;
using ::ai::intrinsic::GripperCmdResult;
using ::intrinsic::skills::ExecuteContext;
using ::intrinsic::skills::ExecuteRequest;
using ::intrinsic::skills::SkillInterface;

namespace gripper_cmd_skill {
namespace {

constexpr char kClusterRouter[] =
    R"(["tcp/zenoh-router.app-intrinsic-base.svc.cluster.local:7447"])";
constexpr char kLocalRouter[] = R"(["tcp/127.0.0.1:7447"])";
constexpr char kTimeoutEnv[] = "ZENOH_QUERY_TIMEOUT_SECONDS";

bool RunningInCluster() {
  return std::getenv("KUBERNETES_SERVICE_HOST") != nullptr ||
         std::filesystem::exists("/etc/intrinsic/runtime_config.pb");
}

// Returns the query timeout from ZENOH_QUERY_TIMEOUT_SECONDS, if set.
std::optional<absl::Duration> QueryTimeoutFromEnv() {
  const char* value = std::getenv(kTimeoutEnv);
  if (value == nullptr || *value == '\0') return std::nullopt;
  int seconds;
  if (!absl::SimpleAtoi(value, &seconds) || seconds <= 0) {
    LOG(WARNING) << "Ignoring invalid " << kTimeoutEnv << "='" << value << "'";
    return std::nullopt;
  }
  return absl::Seconds(seconds);
}

// The first reply to the query, the error if the query timed out, or nullopt
// if the query finished without a reply.
using FirstReply = std::optional<zenoh::Reply>;

absl::StatusOr<FirstReply> ReceiveFirstReply(
    const zenoh::channels::FifoHandler<zenoh::Reply>& replies,
    std::optional<absl::Duration> timeout) {
  if (!timeout.has_value()) {
    auto reply = replies.recv();
    if (std::holds_alternative<zenoh::Reply>(reply)) {
      return FirstReply(std::move(std::get<zenoh::Reply>(reply)));
    }
    return FirstReply();
  }
  const absl::Time deadline = absl::Now() + *timeout;
  while (absl::Now() < deadline) {
    auto reply = replies.try_recv();
    if (std::holds_alternative<zenoh::Reply>(reply)) {
      return FirstReply(std::move(std::get<zenoh::Reply>(reply)));
    }
    if (std::get<zenoh::channels::RecvError>(reply) ==
        zenoh::channels::RecvError::Z_DISCONNECTED) {
      return FirstReply();
    }
    absl::SleepFor(absl::Milliseconds(5));
  }
  return absl::DeadlineExceededError(
      absl::StrCat("Zenoh query timed out after ",
                   absl::ToInt64Seconds(*timeout), " seconds"));
}

}  // namespace

std::string ConnectEndpoints() {
  // ZENOH_CONFIG_OVERRIDE has the form "key=value;key=value".
  if (const char* override_config = std::getenv("ZENOH_CONFIG_OVERRIDE")) {
    for (absl::string_view entry :
         absl::StrSplit(override_config, ';', absl::SkipEmpty())) {
      std::pair<absl::string_view, absl::string_view> kv =
          absl::StrSplit(entry, absl::MaxSplits('=', 1));
      if (kv.first == "connect/endpoints") return std::string(kv.second);
    }
  }
  return RunningInCluster() ? kClusterRouter : kLocalRouter;
}

absl::StatusOr<GripperCmdResult> CallGateway(
    const GripperCmdParams& params, std::optional<absl::Duration> timeout) {
  zenoh::Config config = zenoh::Config::create_default();
  zenoh::ZResult err;
  config.insert_json5("connect/endpoints", ConnectEndpoints(), &err);
  if (err == Z_OK) {
    config.insert_json5("scouting/multicast/enabled", "false", &err);
  }
  if (err != Z_OK) {
    return absl::InternalError("Failed to configure Zenoh in skill");
  }
  std::optional<zenoh::Session> session;
  try {
    session.emplace(zenoh::Session::open(std::move(config)));
  } catch (const zenoh::ZException& e) {
    LOG(ERROR) << "Zenoh session: " << e.what();
    return absl::InternalError("Failed to open Zenoh session in skill");
  }

  std::string payload;
  params.SerializeToString(&payload);
  zenoh::Session::GetOptions options;
  options.payload = zenoh::Bytes(std::move(payload));
  if (timeout.has_value()) {
    // Leave the deadline to ReceiveFirstReply, so that a timeout is reported
    // as such rather than as a reply error from Zenoh.
    options.timeout_ms = absl::ToInt64Milliseconds(*timeout + absl::Seconds(1));
  }
  LOG(INFO) << "Sending Zenoh query to key: " << kQueryKey;
  std::optional<zenoh::channels::FifoHandler<zenoh::Reply>> replies;
  try {
    replies.emplace(session->get(zenoh::KeyExpr(kQueryKey), "",
                                 zenoh::channels::FifoChannel(16),
                                 std::move(options)));
  } catch (const zenoh::ZException& e) {
    LOG(ERROR) << "Zenoh get: " << e.what();
    return absl::InternalError("Failed to send Zenoh query");
  }

  INTR_ASSIGN_OR_RETURN(FirstReply reply, ReceiveFirstReply(*replies, timeout));
  if (!reply.has_value()) {
    return absl::InternalError(absl::StrCat(
        "Zenoh call failed: Zenoh query dropped without receiving a reply on "
        "key: ",
        kQueryKey));
  }
  if (!reply->is_ok()) {
    LOG(ERROR) << "Zenoh reply error: "
               << reply->get_err().get_payload().as_string();
    return absl::InternalError("Zenoh call failed: Zenoh reply error");
  }
  const zenoh::Sample& sample = reply->get_ok();
  const std::vector<uint8_t> data = sample.get_payload().as_vector();
  LOG(INFO) << "Received Zenoh response on key '"
            << sample.get_keyexpr().as_string_view() << "' (" << data.size()
            << " bytes)";
  GripperCmdResult result;
  if (!result.ParseFromArray(data.data(), static_cast<int>(data.size()))) {
    return absl::InternalError("Failed to parse response proto");
  }
  return result;
}

}  // namespace gripper_cmd_skill

std::unique_ptr<SkillInterface> GripperCmdSkill::CreateSkill() {
  return std::make_unique<GripperCmdSkill>();
}

absl::StatusOr<std::unique_ptr<google::protobuf::Message>>
GripperCmdSkill::Execute(const ExecuteRequest& request,
                         ExecuteContext& context) {
  LOG(INFO) << "GripperCmdSkill::Execute via Zenoh C++";
  INTR_ASSIGN_OR_RETURN(auto params, request.params<GripperCmdParams>());
  INTR_ASSIGN_OR_RETURN(
      GripperCmdResult result,
      gripper_cmd_skill::CallGateway(
          params, gripper_cmd_skill::QueryTimeoutFromEnv()));
  // The gateway reports failures, such as an aborted goal or an unreachable
  // action server, in the status. An empty status is a success too.
  if (!result.status().empty() && result.status() != "SUCCEEDED") {
    LOG(ERROR) << "GripperCmdSkill action failed with status: "
               << result.status();
    return absl::InternalError(
        absl::StrCat("Skill action failed: ", result.status()));
  }
  LOG(INFO) << "GripperCmdSkill completed successfully.";
  return std::make_unique<GripperCmdResult>(std::move(result));
}
