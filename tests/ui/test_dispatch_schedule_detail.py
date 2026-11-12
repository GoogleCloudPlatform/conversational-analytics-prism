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

"""HTTP callback dispatch tests for the Schedule Detail page."""

from __future__ import annotations

import datetime
import json

from prism.common.schemas.execution import RunStatus
from prism.common.schemas.schedule import ScheduleCadence
from prism.common.schemas.schedule import ScheduleSchema
from prism.server.models.run import Run
from prism.server.models.schedule import EvalSchedule
from prism.ui.components import schedule_components
from prism.ui.ids import ScheduleDetailIds
from prism.ui.ids import ScheduleIds
from prism.ui.pages import schedule_detail as schedule_detail_page
from tests.ui import dash_http
from tests.ui.test_dispatch_schedules import _seed_schedule

_UTC = datetime.timezone.utc


def _detail_dep(deps):
  return dash_http.find(
      deps, f"{ScheduleDetailIds.STATS}.children", triggered_by="url"
  )


def _render(dash_client, deps, pathname: str):
  response = dash_http.fire_url(dash_client, _detail_dep(deps), pathname, "")
  return response


def test_detail_page_renders_active_schedule_with_runs_and_actions(
    dash_client, callback_errors, db_session, seeded
):
  """Active schedule shows config, upcoming runs, history, and all actions."""
  sched = _seed_schedule(
      db_session, seeded.agent_id, seeded.suite_id, name="Detail Daily"
  )
  # Attribute the seeded run to this schedule so Run History has a row.
  db_session.get(Run, seeded.run_id).schedule_id = sched.id
  db_session.commit()

  deps = dash_http.dependencies(dash_client)
  resp = _render(dash_client, deps, f"/schedules/view/{sched.id}")
  assert resp.status_code == 200
  body = dash_http.body(resp)["response"]
  dumped = json.dumps(body)

  assert body[ScheduleDetailIds.TITLE]["children"] == "Detail Daily"
  assert "Schedules" in json.dumps(body[ScheduleDetailIds.BREADCRUMBS])
  assert "Active" in json.dumps(body[ScheduleDetailIds.STATUS_CONTAINER])

  actions = json.dumps(body[ScheduleDetailIds.ACTIONS])
  for action_id in (
      ScheduleIds.BTN_RUN_NOW,
      ScheduleIds.BTN_TOGGLE_PAUSE,
      ScheduleIds.BTN_EDIT,
      ScheduleIds.BTN_ARCHIVE_ROW,
  ):
    assert action_id in actions
  assert ScheduleIds.BTN_RESTORE_ROW not in actions
  assert "Pause" in actions

  stats = json.dumps(body[ScheduleDetailIds.STATS])
  assert f"/agents/view/{seeded.agent_id}" in stats
  assert f"/test_suites/view/{seeded.suite_id}" in stats
  assert "Daily at 09:00" in stats
  assert "Daily at 09:00 (UTC)" not in stats

  config = json.dumps(body[ScheduleDetailIds.CONFIG_CONTAINER])
  assert "0 9 * * *" in config
  assert "Concurrency" in config
  assert "Last modified" not in config
  assert "Last dispatch failed" not in config

  upcoming = json.dumps(body[ScheduleDetailIds.UPCOMING_CONTAINER])
  assert "Run 1" in upcoming
  assert "Run 5" in upcoming

  runs = json.dumps(body[ScheduleDetailIds.RUNS_CONTAINER])
  assert f"/evaluations/runs/{seeded.run_id}" in runs
  assert f"/evaluations?schedule_id={sched.id}" in runs
  assert "No runs yet" not in dumped
  callback_errors.assert_none()


def test_detail_page_paused_archived_and_failed_dispatch_states(
    dash_client, callback_errors, db_session, seeded
):
  """Paused and archived schedules swap actions and explain missing runs."""
  sched = _seed_schedule(
      db_session,
      seeded.agent_id,
      seeded.suite_id,
      name="Paused Weekly",
      is_enabled=False,
  )
  row = db_session.get(EvalSchedule, sched.id)
  row.last_error_message = "PermissionDenied: caller lacks access"
  db_session.commit()

  deps = dash_http.dependencies(dash_client)
  paused = dash_http.body(
      _render(dash_client, deps, f"/schedules/view/{sched.id}")
  )["response"]
  assert "Paused" in json.dumps(paused[ScheduleDetailIds.STATUS_CONTAINER])
  assert "Last Dispatch Failed" in json.dumps(
      paused[ScheduleDetailIds.STATUS_CONTAINER]
  )
  assert "Resume" in json.dumps(paused[ScheduleDetailIds.ACTIONS])
  assert "Resume the schedule" in json.dumps(
      paused[ScheduleDetailIds.UPCOMING_CONTAINER]
  )
  assert "Last dispatch failed" in json.dumps(
      paused[ScheduleDetailIds.CONFIG_CONTAINER]
  )
  assert "No runs yet" in json.dumps(paused[ScheduleDetailIds.RUNS_CONTAINER])

  row.is_archived = True
  db_session.commit()
  archived = dash_http.body(
      _render(dash_client, deps, f"/schedules/view/{sched.id}")
  )["response"]
  actions = json.dumps(archived[ScheduleDetailIds.ACTIONS])
  assert ScheduleIds.BTN_RESTORE_ROW in actions
  assert ScheduleIds.BTN_RUN_NOW not in actions
  assert ScheduleIds.BTN_EDIT not in actions
  assert "Archived schedules do not run" in json.dumps(
      archived[ScheduleDetailIds.UPCOMING_CONTAINER]
  )
  callback_errors.assert_none()


def test_detail_page_not_found_and_foreign_paths(
    dash_client, callback_errors, db_session, seeded
):
  """Unknown ids render a not-found alert and other pages are left alone."""
  del db_session, seeded
  deps = dash_http.dependencies(dash_client)

  missing = dash_http.body(
      _render(dash_client, deps, "/schedules/view/424242")
  )["response"]
  assert missing[ScheduleDetailIds.TITLE]["children"] == "Schedule not found"
  assert "Schedule 424242 was not found." in json.dumps(
      missing[ScheduleDetailIds.CONFIG_CONTAINER]
  )

  non_numeric = dash_http.body(
      _render(dash_client, deps, "/schedules/view/none")
  )["response"]
  assert "Schedule none was not found." in json.dumps(
      non_numeric[ScheduleDetailIds.CONFIG_CONTAINER]
  )

  for foreign_path in ("/schedules", "/agents"):
    foreign = _render(dash_client, deps, foreign_path)
    assert foreign.status_code in (200, 204)
    assert ScheduleDetailIds.TITLE not in dash_http.body(foreign).get(
        "response", {}
    )

  # The update signal redraws the page in place.
  refresh = dash_http.fire(
      dash_client,
      _detail_dep(deps),
      {
          "url.pathname": "/schedules/view/424242",
          f"{ScheduleDetailIds.POLL_INTERVAL}.n_intervals": 3,
          f"{ScheduleIds.UPDATE_SIGNAL}.data": {"ts": 1},
      },
      changed=[f"{ScheduleIds.UPDATE_SIGNAL}.data"],
  )
  assert refresh.status_code == 200
  assert ScheduleDetailIds.ACTIONS in dash_http.body(refresh)["response"]
  callback_errors.assert_none()


def test_poll_refreshes_data_but_not_the_header_actions(
    dash_client, callback_errors, db_session, seeded
):
  """A poll tick must not replace the Run Now button mid-click."""
  sched = _seed_schedule(
      db_session, seeded.agent_id, seeded.suite_id, name="Polled Schedule"
  )
  deps = dash_http.dependencies(dash_client)
  tick = dash_http.fire(
      dash_client,
      _detail_dep(deps),
      {
          "url.pathname": f"/schedules/view/{sched.id}",
          f"{ScheduleDetailIds.POLL_INTERVAL}.n_intervals": 4,
          f"{ScheduleIds.UPDATE_SIGNAL}.data": None,
      },
      changed=[f"{ScheduleDetailIds.POLL_INTERVAL}.n_intervals"],
  )
  assert tick.status_code == 200
  written = dash_http.body(tick)["response"]
  for untouched in (
      ScheduleDetailIds.TITLE,
      ScheduleDetailIds.BREADCRUMBS,
      ScheduleDetailIds.ACTIONS,
  ):
    assert untouched not in written
  for refreshed in (
      ScheduleDetailIds.STATUS_CONTAINER,
      ScheduleDetailIds.STATS,
      ScheduleDetailIds.RUNS_CONTAINER,
  ):
    assert refreshed in written
  callback_errors.assert_none()


def test_detail_layout_and_component_helpers():
  """The skeleton holds the modal and shared signal; helpers cover branches."""
  dumped_layout = repr(schedule_detail_page.layout())
  for component_id in (
      ScheduleDetailIds.TITLE,
      ScheduleDetailIds.ACTIONS,
      ScheduleDetailIds.STATS,
      ScheduleDetailIds.RUNS_CONTAINER,
      ScheduleIds.MODAL,
      ScheduleIds.UPDATE_SIGNAL,
  ):
    assert component_id in dumped_layout

  now = datetime.datetime(2026, 10, 30, 12, 0, tzinfo=_UTC)
  base = dict(
      id=5,
      name="Helper Schedule",
      agent_id=1,
      test_suite_id=2,
      cadence=ScheduleCadence.WEEKLY,
      cron_expression="0 8 * * 1",
      timezone="America/New_York",
      created_at=now,
      modified_at=now,
  )
  active = ScheduleSchema(**base, next_run_at=now)
  pending = ScheduleSchema(**base)

  assert schedule_components.schedule_detail_href(5) == "/schedules/view/5"
  # pylint: disable=protected-access
  assert schedule_components._next_run_text(pending) == "Pending calculation"
  assert "2026-10-30" in schedule_components._next_run_text(active)
  # pylint: enable=protected-access

  # Missing names fall back to ids in the stats and the list.
  stats = repr(schedule_components.render_schedule_detail_stats(pending))
  assert "Agent 1" in stats
  assert "Suite 2" in stats
  table = repr(schedule_components.render_schedule_table([pending]))
  assert "Agent 1" in table
  assert "Suite 2" in table
  assert "Pending calculation" in table

  upcoming = repr(
      schedule_components.render_upcoming_runs_card(
          active, [datetime.datetime(2026, 11, 2, 13, 0, tzinfo=_UTC)]
      )
  )
  assert "2026-11-02 08:00 EST" in upcoming
  assert "UTC" in upcoming

  config = repr(schedule_components.render_schedule_config_card(active))
  assert "America/New_York" in config
  assert "Weekly" in config
  assert "Off" in config

  last_run = ScheduleSchema(
      **base, last_run_id=9, last_run_status=RunStatus.COMPLETED
  )
  assert "#9" in repr(schedule_components.render_schedule_table([last_run]))
