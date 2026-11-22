"""Keep Log4j's reflectively created flow factory in merged AOT metadata."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from mdaf import build_native_image_config


class LoggingReflectionTests(unittest.TestCase):
    def test_log4j_flow_factory_constructor_survives_metadata_merging(self):
        maintained = json.loads((ROOT / "compiler/reflection-config.json").read_text())
        documents, conflicts = build_native_image_config(maintained, [], {})
        self.assertFalse(conflicts)
        entries = {item["name"]: item for item in documents["reflect-config.json"]}
        for factory in ("message.DefaultFlowMessageFactory", "message.ParameterizedMessageFactory",
                        "core.impl.Log4jContextFactory"):
            with self.subTest(factory=factory):
                entry = entries["org.apache.logging.log4j." + factory]
                self.assertIn({"name": "<init>", "parameterTypes": []}, entry["methods"])
        for name in ("Date", "ThreadName", "Level", "Logger", "Message", "LineSeparator", "Throwable"):
            with self.subTest(converter=name):
                entry = entries["org.apache.logging.log4j.core.pattern." + name + "PatternConverter"]
                parameters = ["java.lang.String[]"]
                if name in ("Message", "Throwable"):
                    parameters.insert(0, "org.apache.logging.log4j.core.config.Configuration")
                self.assertIn({"name": "newInstance", "parameterTypes": parameters}, entry["methods"])


if __name__ == "__main__":
    unittest.main()
