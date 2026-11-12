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

"""Callbacks for the Schedule Detail page."""

import re
from typing import Any

import dash
from prism.client import get_client
from prism.ui.components import schedule_components
from prism.ui.constants import CP
from prism.ui.ids import ScheduleDetailIds as Ids
from prism.ui.ids import ScheduleIds
from prism.ui.utils import typed_callback

_DETAIL_PATH = re.compile(r"^/schedules/view/(?P<schedule_id>[^/]+)/?$")
_RECENT_RUN_LIMIT = 10
_UPCOMING_RUN_COUNT = 5
_OUTPUT_COUNT = 8


def _build_schedule_detail_view(pathname: str | None) -> tuple[Any, ...]:
  """Builds every container on `/schedules/view/<schedule_id>`."""
  match = _DETAIL_PATH.match(pathname or "")
  if not match:
    return (dash.no_update,) * _OUTPUT_COUNT

  raw_id = match.group("schedule_id")
  client = get_client()
  schedule = (
      client.schedules.get_schedule(int(raw_id)) if raw_id.isdigit() else None
  )
  if schedule is None:
    return (
        "Schedule not found",
        schedule_components.render_schedule_detail_breadcrumbs("Not found"),
        None,
        [],
        [],
        schedule_components.render_schedule_not_found(raw_id),
        None,
        None,
    )

  upcoming = []
  if schedule.is_enabled and not schedule.is_archived:
    upcoming = client.schedules.preview_next_runs(
        schedule.cron_expression,
        timezone=schedule.timezone,
        count=_UPCOMING_RUN_COUNT,
    )
  runs = list(
      client.runs.list_runs(
          schedule_id=schedule.id,
          include_archived=True,
          limit=_RECENT_RUN_LIMIT,
      )
  )
  return (
      schedule.name,
      schedule_components.render_schedule_detail_breadcrumbs(schedule.name),
      schedule_components.render_schedule_detail_status(schedule),
      schedule_components.render_schedule_detail_actions(schedule),
      schedule_components.render_schedule_detail_stats(schedule),
      schedule_components.render_schedule_config_card(schedule),
      schedule_components.render_upcoming_runs_card(schedule, upcoming),
      schedule_components.render_schedule_runs_card(schedule, runs),
  )


@typed_callback(
    output=[
        (Ids.TITLE, CP.CHILDREN),
        (Ids.BREADCRUMBS, CP.CHILDREN),
        (Ids.STATUS_CONTAINER, CP.CHILDREN),
        (Ids.ACTIONS, CP.CHILDREN),
        (Ids.STATS, CP.CHILDREN),
        (Ids.CONFIG_CONTAINER, CP.CHILDREN),
        (Ids.UPCOMING_CONTAINER, CP.CHILDREN),
        (Ids.RUNS_CONTAINER, CP.CHILDREN),
    ],
    inputs=[
        ("url", CP.PATHNAME),
        (Ids.POLL_INTERVAL, "n_intervals"),
        (ScheduleIds.UPDATE_SIGNAL, CP.DATA),
    ],
)
def render_schedule_detail(
    pathname: str | None,
    unused_intervals: int | None,
    unused_signal: Any,
):
  """Renders the Schedule Detail page on load, poll, and after any action.

  The poll leaves the title, breadcrumbs, and header actions alone. Those only
  change through a user action, which fires the update signal. Redrawing the
  actions on every tick replaced the Run Now button while its click was in
  flight, and Dash dropped that response, toast and all.

  Args:
    pathname: The page path, which carries the schedule id.
    unused_intervals: The poll tick count.
    unused_signal: The update signal written after a schedule action.

  Returns:
    New children for every container, or no_update for each.
  """
  del unused_intervals, unused_signal
  view = _build_schedule_detail_view(pathname)
  if dash.ctx.triggered_id != Ids.POLL_INTERVAL:
    return view
  title, breadcrumbs, status, _, *rest = view
  del title, breadcrumbs
  return (dash.no_update, dash.no_update, status, dash.no_update, *rest)
