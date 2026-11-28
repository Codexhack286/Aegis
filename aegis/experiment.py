import os

from aegis.core import STRATEGIES


def flagsmith_origin():
    mode = os.environ.get("FLAGSMITH_MODE", "self-hosted").strip().lower()
    if mode == "cloud":
        return None
    if mode == "self-hosted":
        return os.environ.get("FLAGSMITH_ORIGIN", "http://localhost:8000").rstrip("/")
    raise ValueError("Set FLAGSMITH_MODE to cloud or self-hosted.")


def flagsmith_key_name(origin):
    return "FLAGSMITH_CLOUD_ENVIRONMENT_KEY" if origin is None else "FLAGSMITH_LOCAL_ENVIRONMENT_KEY"


def flagsmith_key(origin):
    if "FLAGSMITH_CLOUD_ENVIRONMENT_KEY" in os.environ or "FLAGSMITH_LOCAL_ENVIRONMENT_KEY" in os.environ:
        return os.environ.get(flagsmith_key_name(origin), "").strip()
    return os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "").strip()


class Experiment:
    def __init__(self):
        origin = flagsmith_origin()
        key = flagsmith_key(origin)
        if not key:
            raise ValueError("Set the SDK environment key for the selected FLAGSMITH_MODE in .env.")
        try:
            from flagsmith import EventProcessorConfig, Flagsmith
        except ImportError as error:
            raise RuntimeError("Install the experiment extra with uv sync --extra experiment.") from error
        flags_options = {}
        event_options = {"max_buffer_items": 1}
        if origin is not None:
            flags_options["api_url"] = f"{origin}/api/v1/"
            event_options["events_api_url"] = f"{origin}/api/"
        self.client = Flagsmith(
            environment_key=key,
            enable_events=True,
            event_processor_config=EventProcessorConfig(**event_options),
            **flags_options,
        )

    def resolve(self, identity):
        flag = self.client.get_identity_flags(identity).get_flag("agent_strategy")
        if not flag.enabled or flag.value not in STRATEGIES:
            raise ValueError("agent_strategy must be enabled and return a known strategy.")
        if not flag.experiment or not flag.experiment.in_experiment or not flag.variant:
            raise ValueError("Identity needs a keyed variation in a running experiment.")
        return flag

    def expose(self, identity):
        return self.expose_flag(identity).value

    def expose_flag(self, identity):
        flag = self.resolve(identity)
        self.client.track_exposure_event("agent_strategy", identifier=identity, value=flag.variant)
        return flag

    def success(self, identity):
        self.client.track_event("task_success", identifier=identity)
