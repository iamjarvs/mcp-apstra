from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.system_health import handle_get_active_system_agent_jobs


def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


class TestHandleGetActiveSystemAgentJobs:
    @pytest.mark.asyncio
    async def test_enriches_active_jobs_with_device_and_blueprint_context(self):
        session = make_session()
        registry = MagicMock()

        raw_jobs = {
            "items": [
                {
                    "job_id": 10,
                    "job_type": "upgrade",
                    "state": "inprogress",
                    "host_id": "agent-1",
                    "current_task": "Check Connectivity",
                    "started": "2026-05-19T12:13:34.648733Z",
                    "created": "2026-05-19T12:13:34.616613Z",
                    "agent_type": "offbox",
                    "is_log_available": True,
                    "error": "",
                }
            ]
        }
        raw_agents = {
            "items": [
                {
                    "id": "agent-1",
                    "config": {
                        "id": "agent-1",
                        "management_ip": "10.88.9.241",
                    },
                    "status": {
                        "system_id": "YS3121120026",
                        "connection_state": "disconnected",
                    },
                    "platform_status": {
                        "platform": "junos",
                        "platform_version": "{'re0': '23.4R2-S7.7'}",
                    },
                    "device_facts": {
                        "hostname": "qfx5120-48ym-2",
                        "device_os_version": "23.4R2-S7.7",
                    },
                }
            ]
        }
        raw_blueprints = {
            "items": [
                {
                    "id": "bp-1",
                    "label": "Fabric A",
                }
            ]
        }
        systems_result = {
            "systems": [
                {
                    "system_id": "YS3121120026",
                    "label": "leaf-2",
                    "hostname": "qfx5120-48ym-2",
                    "role": "leaf",
                    "management_level": "full_control",
                    "deploy_mode": "deploy",
                }
            ]
        }

        with patch(
            "handlers.system_health.live_data_client.get_active_system_agent_jobs",
            new=AsyncMock(return_value=raw_jobs),
        ), patch(
            "handlers.system_health.live_data_client.get_system_agents",
            new=AsyncMock(return_value=raw_agents),
        ), patch(
            "handlers.system_health.live_data_client.get_blueprints",
            new=AsyncMock(return_value=raw_blueprints),
        ), patch(
            "handlers.system_health.handle_get_systems",
            new=AsyncMock(return_value=systems_result),
        ) as mock_get_systems:
            result = await handle_get_active_system_agent_jobs([session], registry)

        assert result["instance"] == "dc-primary"
        assert result["has_active_jobs"] is True
        assert result["active_job_count"] == 1
        assert result["summary"]["by_job_type"] == {"upgrade": 1}
        assert mock_get_systems.await_count == 1

        job = result["active_jobs"][0]
        assert job["device_identified"] is True
        assert job["device"]["system_id"] == "YS3121120026"
        assert job["device"]["hostname"] == "qfx5120-48ym-2"
        assert job["device"]["management_ip"] == "10.88.9.241"
        assert job["blueprint_match_count"] == 1
        assert job["blueprint_matches"][0]["blueprint_id"] == "bp-1"
        assert job["blueprint_matches"][0]["blueprint_label"] == "Fabric A"

    @pytest.mark.asyncio
    async def test_skips_agent_and_blueprint_lookups_when_no_jobs_exist(self):
        session = make_session()
        registry = MagicMock()

        with patch(
            "handlers.system_health.live_data_client.get_active_system_agent_jobs",
            new=AsyncMock(return_value={"items": []}),
        ), patch(
            "handlers.system_health.live_data_client.get_system_agents",
            new=AsyncMock(),
        ) as mock_get_agents, patch(
            "handlers.system_health.live_data_client.get_blueprints",
            new=AsyncMock(),
        ) as mock_get_blueprints:
            result = await handle_get_active_system_agent_jobs([session], registry)

        assert result["has_active_jobs"] is False
        assert result["active_job_count"] == 0
        assert result["active_jobs"] == []
        mock_get_agents.assert_not_awaited()
        mock_get_blueprints.assert_not_awaited()