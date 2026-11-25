"""The TouchController transport rewrite: native methods become host-bridge calls."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import prepare_fabric_aot


class TouchControllerTransportTests(unittest.TestCase):
    def test_ios_transport_is_rewritten_onto_the_host_bridge(self):
        source = (ROOT / 'interop/fabric/tool/mcfao/build/RuntimeLowering.java').read_text()
        self.assertIn('top/fifthlight/touchcontroller/common/platform/ios/Transport', source)
        self.assertIn('mcfao/aot/IosTransport', source)
        # The three native entry points keep their descriptors and lose ACC_NATIVE.
        for name in ('init', 'receive', 'send'):
            self.assertIn('"' + name + '"', source)

    def test_every_unreachable_host_transport_is_named(self):
        source = (ROOT / 'interop/fabric/tool/mcfao/build/RuntimeLowering.java').read_text()
        for platform in ('android/Transport', 'nativebridge/Interface',
                         'wayland/Interface', 'win32/Interface',
                         'proxy/client/nativebridge/NativeInterface'):
            self.assertIn(platform, source)

    def test_the_bridge_idles_instead_of_throwing(self):
        """An uninstalled host must not break the Mod's per-frame poll."""
        bridge = (ROOT / 'interop/fabric/tool/mcfao/aot/IosTransport.java').read_text()
        self.assertIn('if (current == null || buffer == null || buffer.length == 0) return 0;', bridge)
        self.assertIn('public static void install(Host replacement)', bridge)

    def test_the_rewritten_init_installs_the_host(self):
        """The Mod calls Transport.init() once; that call must reach TouchHost.

        An init that only flips a flag leaves the host field null, which the closed-world image
        folds to a constant miss and prunes the whole TouchHost class.
        """
        lowering = (ROOT / 'interop/fabric/tool/mcfao/build/RuntimeLowering.java').read_text()
        self.assertIn('"init", "()V", false', lowering)
        bridge = (ROOT / 'interop/fabric/tool/mcfao/aot/IosTransport.java').read_text()
        self.assertIn('public static void init()', bridge)
        self.assertIn('install(new TouchHost())', bridge)

    def test_clearing_the_queue_releases_the_mods_pointers(self):
        """A cancel or rotation must not leave a control held down in the Mod."""
        queue = (ROOT / 'ios/glfw-lite/touch_input.c').read_text()
        self.assertIn('static void abandon(const char *reason)', queue)
        self.assertIn('push(encodeClear(clear), clear)', queue)
        for call in ('abandon("clear")', 'abandon("disconnect")', 'abandon("pointer capacity")'):
            self.assertIn(call, queue)

    def test_native_symbols_are_bound_lazily(self):
        """Transport.<clinit> can run before GLFW opens its library."""
        host = (ROOT / 'interop/fabric/tool/mcfao/aot/TouchHost.java').read_text()
        self.assertIn('private boolean ensure()', host)
        self.assertIn('if (library == null) return false;', host)
        self.assertNotIn('private final long frame = symbol(', host)

    def test_haptics_reach_the_surface(self):
        """TouchHost forwards VibrateMessage as kind 4; the native side must act on it.

        Dropping the kind leaves the Mod's vibration setting on while nothing happens, which
        reads as a broken control rather than a missing feature.
        """
        host = (ROOT / 'interop/fabric/tool/mcfao/aot/TouchHost.java').read_text()
        self.assertIn('JNI.invokePV(4L, kind, command)', host)
        native = (ROOT / 'ios/glfw-lite/glfw_lite.c').read_text()
        self.assertIn('else if (kind == 4) faot_surface_vibrate(value)', native)
        surface = (ROOT / 'ios/glfw-lite/uikit_surface.m').read_text()
        self.assertIn('void faot_surface_vibrate(int kind)', surface)
        self.assertIn('UIImpactFeedbackGenerator', surface)

    def test_a_failed_config_initializer_keeps_the_bridge_enabled(self):
        """The Mod's config holder resolves a sandbox directory during class init.

        When that throws, the JVM raises ExceptionInInitializerError, which is a LinkageError
        rather than a RuntimeException. Catching only RuntimeException would let the failure
        escape the constructor, leave the host uninstalled, and silently kill input.
        """
        host = (ROOT / 'interop/fabric/tool/mcfao/aot/TouchHost.java').read_text()
        self.assertIn('catch (RuntimeException | LinkageError unavailable)', host)
        self.assertIn('return true;', host)

    def test_nested_libraries_of_another_minecraft_line_are_skipped(self):
        """TouchController bundles one Jar per line; only the target line may be validated."""
        satisfies = prepare_fabric_aot.nested_targets_version
        self.assertTrue(satisfies({'depends': {'minecraft': ['26.1', '26.1.1', '26.1.2']}}, '26.1.2'))
        self.assertFalse(satisfies({'depends': {'minecraft': ['1.21.9', '1.21.10']}}, '26.1.2'))
        self.assertTrue(satisfies({'depends': {'minecraft': '>=1.21.2'}}, '26.1.2'))
        self.assertFalse(satisfies({'depends': {'minecraft': '<1.21.3'}}, '26.1.2'))
        self.assertFalse(satisfies({'depends': {'minecraft': '~26.3-'}}, '26.1.2'))
        # A missing declaration keeps the Jar: the Loader loads it.
        self.assertTrue(satisfies({'depends': {}}, '26.1.2'))


if __name__ == '__main__':
    unittest.main()
