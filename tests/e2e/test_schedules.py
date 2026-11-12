# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Browser E2E journeys for Scheduled Evaluations."""

from __future__ import annotations

import datetime
import time

from playwright.sync_api import expect
from playwright.sync_api import Page
from prism.common.schemas.assertion import AssertionType
from prism.common.schemas.execution import RunStatus
from prism.server.models.run import Run
from prism.server.models.schedule import EvalSchedule
from prism.ui.ids import EvaluationIds
from prism.ui.ids import ScheduleDetailIds
from prism.ui.ids import ScheduleIds
from prism.ui.ids import ShellIds
import pytest
import sqlalchemy
from tests.e2e import traces
from tests.e2e.conftest import select_option

pytestmark = pytest.mark.e2e

_RUN_TIMEOUT_MS = 120_000
_UTC = datetime.timezone.utc


def test_manage_schedule_lifecycle_in_ui(page: Page, base_url: str, seed):
  """Create, pause, resume, edit, archive, and restore a schedule in Chromium."""
  agent = seed.agent(name="Nightly Ops Agent")
  suite = seed.suite(name="Nightly Ops Suite")
  seed.example(
      suite,
      question=traces.QUESTION,
      assertions=[(AssertionType.TEXT_CONTAINS, {"value": "128"})],
  )

  page.goto(base_url)
  page.wait_for_load_state("networkidle")

  # Navigate via the top shell navbar.
  page.locator(f"#{ShellIds.NAV_SCHEDULES}").click()
  page.wait_for_url("**/schedules", timeout=15_000)
  expect(page.get_by_text("No evaluation schedules configured")).to_be_visible()

  # Open Create Schedule modal, configure cadence and timezone, verify preview.
  page.get_by_role("button", name="New Schedule", exact=True).click()
  expect(page.locator(f"#{ScheduleIds.MODAL_TITLE}")).to_have_text(
      "New Evaluation Schedule"
  )

  page.locator(f"#{ScheduleIds.INPUT_NAME}").fill("Morning Revenue Gate")
  select_option(page, ScheduleIds.SELECT_AGENT, agent.name)
  select_option(page, ScheduleIds.SELECT_SUITE, suite.name)

  # Switch cadence preset to Hourly and verify live preview updates.
  page.locator(f"#{ScheduleIds.SEGMENT_CADENCE}").get_by_text(
      "Hourly", exact=True
  ).click()
  expect(page.locator(f"#{ScheduleIds.PREVIEW_CONTAINER}")).to_contain_text(
      "Hourly at :00"
  )
  expect(page.locator(f"#{ScheduleIds.PREVIEW_CONTAINER}")).to_contain_text(
      "Run 3"
  )

  page.locator(f"#{ScheduleIds.BTN_SAVE}").click()

  # Table renders a read-only Active row with no inline actions.
  table = page.locator(f"#{ScheduleIds.LIST_CONTAINER}")
  expect(table).to_contain_text("Morning Revenue Gate")
  expect(table).to_contain_text("Active")
  expect(table.get_by_role("button", name="Pause", exact=True)).to_have_count(0)

  # Open the Schedule Detail page from the row.
  table.get_by_role("link", name="Morning Revenue Gate", exact=True).click()
  page.wait_for_url("**/schedules/view/*", timeout=15_000)
  expect(page.locator(f"#{ScheduleDetailIds.TITLE}")).to_have_text(
      "Morning Revenue Gate"
  )
  status = page.locator(f"#{ScheduleDetailIds.STATUS_CONTAINER}")
  actions = page.locator(f"#{ScheduleDetailIds.ACTIONS}")
  expect(status).to_contain_text("Active")
  expect(
      page.locator(f"#{ScheduleDetailIds.CONFIG_CONTAINER}")
  ).to_contain_text("0 * * * *")
  expect(
      page.locator(f"#{ScheduleDetailIds.UPCOMING_CONTAINER}")
  ).to_contain_text("Run 5")
  expect(page.locator(f"#{ScheduleDetailIds.RUNS_CONTAINER}")).to_contain_text(
      "No runs yet"
  )

  # Pause and Resume from the detail header.
  actions.get_by_role("button", name="Pause", exact=True).click()
  expect(status).to_contain_text("Paused")
  expect(
      page.locator(f"#{ScheduleDetailIds.UPCOMING_CONTAINER}")
  ).to_contain_text("Resume the schedule")

  actions.get_by_role("button", name="Resume", exact=True).click()
  expect(status).to_contain_text("Active")

  # Edit opens the shared modal prefilled, and Save redraws the page in place.
  actions.get_by_role("button", name="Edit", exact=True).click()
  expect(page.locator(f"#{ScheduleIds.MODAL_TITLE}")).to_contain_text(
      "Edit Schedule: Morning Revenue Gate"
  )
  page.locator(f"#{ScheduleIds.INPUT_NAME}").fill("Hourly Revenue Gate")
  page.locator(f"#{ScheduleIds.BTN_SAVE}").click()
  expect(page.locator(f"#{ScheduleDetailIds.TITLE}")).to_have_text(
      "Hourly Revenue Gate"
  )

  # Cancel closes the modal without saving.
  actions.get_by_role("button", name="Edit", exact=True).click()
  page.locator(f"#{ScheduleIds.INPUT_NAME}").fill("Discarded Name")
  page.locator(f"#{ScheduleIds.BTN_CANCEL}").click()
  expect(page.locator(f"#{ScheduleDetailIds.TITLE}")).to_have_text(
      "Hourly Revenue Gate"
  )

  # Archive leaves only Restore, and Restore brings it back paused.
  actions.get_by_role("button", name="Archive", exact=True).click()
  expect(status).to_contain_text("Archived")
  expect(actions.get_by_role("button", name="Edit", exact=True)).to_have_count(
      0
  )
  actions.get_by_role("button", name="Restore", exact=True).click()
  expect(status).to_contain_text("Paused")

  # Breadcrumb back to the list shows the renamed, paused schedule.
  page.locator(f"#{ScheduleDetailIds.BREADCRUMBS}").get_by_role(
      "link", name="Schedules"
  ).click()
  page.wait_for_url("**/schedules", timeout=15_000)
  expect(table).to_contain_text("Hourly Revenue Gate")
  expect(table).to_contain_text("Paused")


def test_deep_link_run_now_and_background_due_pickup_complete_end_to_end(
    page: Page, base_url: str, seed, db, cassettes
):
  """Schedule deep-link prefill + Run Now + automatic due worker pickup complete."""
  agent = seed.agent(name="Scheduled Worker Agent")
  suite = seed.suite(name="Scheduled Worker Suite")
  seed.example(
      suite,
      question=traces.QUESTION,
      assertions=[
          (AssertionType.TEXT_CONTAINS, {"value": "128"}),
          (AssertionType.QUERY_CONTAINS, {"value": "COUNT(*)"}),
      ],
  )
  cassettes.agent_context(agent)
  cassettes.datasource_kind(agent)
  cassettes.ask_question(agent, traces.QUESTION)

  # 1. Deep-link from Test Suite detail page into prefilled Schedule modal.
  page.goto(f"{base_url}/test_suites/view/{suite.id}")
  page.wait_for_load_state("networkidle")
  page.get_by_role("link", name="Schedule", exact=True).click()
  page.wait_for_url("**/schedules?create=1**", timeout=15_000)

  expect(page.locator(f"#{ScheduleIds.INPUT_NAME}")).to_have_value(
      f"Daily {suite.name}"
  )
  select_option(page, ScheduleIds.SELECT_AGENT, agent.name)
  page.locator(f"#{ScheduleIds.BTN_SAVE}").click()

  table = page.locator(f"#{ScheduleIds.LIST_CONTAINER}")
  expect(table).to_contain_text(f"Daily {suite.name}")

  # 2. Open the schedule, trigger Run Now, and watch the run finish at 100%.
  table.get_by_role("link", name=f"Open schedule Daily {suite.name}").click()
  page.wait_for_url("**/schedules/view/*", timeout=15_000)
  page.locator(f"#{ScheduleDetailIds.ACTIONS}").get_by_role(
      "button", name="Run Now", exact=True
  ).click()
  runs_card = page.locator(f"#{ScheduleDetailIds.RUNS_CONTAINER}")
  expect(runs_card).to_contain_text("Scheduled", timeout=15_000)

  runs_card.get_by_role("link", name="View all in Evaluations").click()
  page.wait_for_url("**/evaluations?schedule_id=*", timeout=15_000)

  run_list = page.locator(f"#{EvaluationIds.RUN_LIST_CONTAINER}")
  expect(run_list).to_contain_text("Scheduled")
  run_list.get_by_role("link", name="View Report").click()
  page.wait_for_url("**/evaluations/runs/*", timeout=15_000)

  expect(page.locator(f"#{EvaluationIds.RUN_STATUS_BADGE}")).to_contain_text(
      "COMPLETED", timeout=_RUN_TIMEOUT_MS, ignore_case=True
  )
  expect(page.locator(f"#{EvaluationIds.RUN_DETAIL_STATS}")).to_contain_text(
      "100.0%"
  )
  expect(page.locator("body")).to_contain_text(f"Schedule: Daily {suite.name}")

  # The provenance badge leads back to the schedule it came from.
  page.locator(f"#{EvaluationIds.RUN_SCHEDULE_BADGE}").click()
  page.wait_for_url("**/schedules/view/*", timeout=15_000)
  expect(page.locator(f"#{ScheduleDetailIds.TITLE}")).to_have_text(
      f"Daily {suite.name}"
  )

  # 3. Seed an immediately-due schedule in PostgreSQL and verify the live
  # WorkerProcessManager background loop claims it and completes Run #2 with
  # zero manual clicks.
  past = datetime.datetime.now(_UTC) - datetime.timedelta(minutes=2)
  due_sched = EvalSchedule(
      name="Auto Due Worker Schedule",
      agent_id=agent.id,
      test_suite_id=suite.id,
      cadence="HOURLY",
      cron_expression="0 * * * *",
      timezone="UTC",
      generate_suggestions=False,
      concurrency=2,
      is_enabled=True,
      next_run_at=past,
  )
  db.add(due_sched)
  db.commit()
  due_sched_id = due_sched.id

  deadline = time.monotonic() + 90.0
  auto_run = None
  while time.monotonic() < deadline:
    db.expire_all()
    auto_run = (
        db.execute(
            sqlalchemy.select(Run).where(Run.schedule_id == due_sched_id)
        )
        .scalars()
        .first()
    )
    if auto_run is not None and auto_run.status == RunStatus.COMPLETED:
      break
    time.sleep(0.5)

  assert auto_run is not None, "Worker loop never claimed the due schedule."
  assert auto_run.status == RunStatus.COMPLETED
  refreshed_sched = db.get(EvalSchedule, due_sched_id)
  assert refreshed_sched.last_run_id == auto_run.id
  assert refreshed_sched.next_run_at > past
