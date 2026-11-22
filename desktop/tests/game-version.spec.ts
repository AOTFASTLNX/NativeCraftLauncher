import { test, expect, type Page } from "@playwright/test";

/**
 * Minecraft version handoff in the desktop UI.
 *
 * The IPC layer is mocked: no Python worker, compiler or device is touched.
 * These tests pin what the user sees and what the GUI actually sends for the
 * new 1.21.1 line, including that a previous pack's version never leaks into
 * the next one.
 */

const modRow = {
  path: "mods/fixture.jar",
  origin: "overrides",
  category: "mods",
  optional: false,
  status: "available",
  selectable: true,
  default_selected: true,
  bytes: 128,
  file_count: 1,
  client_requirement: "unknown",
  requirement_basis: "undeclared",
};

/** A static preflight report for one pack, shaped like tools/preflight.py. */
function packReport(version: "1.21.1" | "26.1.2", source: string) {
  const mod = {
    id: "fixture_mod",
    name: "Fixture Mod",
    version: "1.0.0",
    path: "mods/fixture.jar",
    environment: "*",
    has_mixins: false,
    has_entrypoints: false,
    has_class_tweaker: false,
    has_language_adapters: false,
    nested_jar_count: 0,
    bundled_native_count: 0,
  };
  return {
    source,
    target_minecraft: version,
    minecraft_version: version,
    status: "STATIC_PREFLIGHT_ONLY",
    source_type: "mrpack",
    mods: [mod],
    mod_inventory: [
      { path: "mods/fixture.jar", origin: "local", status: "metadata_read", findings: [] },
    ],
    findings: [],
    fatal: false,
    aot_ios_verified: false,
    pack_name: "Version fixture " + version,
    pack_version: "1",
    fabric_loader_version: "0.19.3",
    manifest_files: 0,
    resources: [{ id: "overrides:mods/fixture.jar", ...modRow }],
    resources_fingerprint: "a".repeat(64),
  };
}

const bootstrap = {
  workspace: "/fixture/source",
  config: "/fixture/config.json",
  configFields: {},
  exportOptions: "/fixture/Export.plist",
  output: "/fixture/builds",
  jobs: [],
  minecraftVersion: "26.1.2",
  versions: ["26.1.2", "1.21.1"],
};

/**
 * Install the mocked IPC. `packs` maps a chosen .mrpack path to its report so
 * a test can switch between two versions without reloading the page.
 */
async function mockDesktop(
  page: Page,
  packs: Record<string, ReturnType<typeof packReport>>,
) {
  await page.addInitScript(
    ({ packs, bootstrap }) => {
      localStorage.setItem(
        "ncl.onboarding.v1",
        JSON.stringify({ version: 1, accepted: true, storageRoot: "/fixture", completed: true }),
      );
      const calls: { command: string; args: Record<string, unknown> }[] = [];
      // The picker queue lives in the page: a Node-side array would be
      // serialized by value and never see later pushes.
      const picks: string[] = [];
      Object.assign(window, {
        isTauri: true,
        __versionCalls: calls,
        __versionPicks: picks,
        __TAURI_INTERNALS__: {
          metadata: {
            currentWindow: { label: "main" },
            currentWebview: { windowLabel: "main", label: "main" },
          },
          transformCallback: () => 1,
          invoke: async (command: string, args: Record<string, unknown>) => {
            calls.push({ command, args });
            if (command === "default_workspace") return "/fixture/source";
            if (command === "system_accent") return "#3478D4";
            if (command === "set_window_theme") return null;
            if (command === "account_action")
              return {
                configured: true,
                verified: true,
                profile: { id: "fixture", name: "Fixture" },
                verifiedAt: Math.floor(Date.now() / 1000),
                verifiedUntil: Math.floor(Date.now() / 1000) + 3600,
              };
            if (command === "plugin:dialog|open") return picks.shift() ?? null;
            if (command === "inspect_pack") {
              const source = String(args.source);
              const report = (packs as Record<string, unknown>)[source];
              if (!report) throw new Error("unexpected source " + source);
              return { report, elapsedMs: 4 };
            }
            if (command !== "workbench_action") return null;
            const action = String(args.action);
            if (action === "bootstrap") return bootstrap;
            if (action === "dependencies")
              return { ready: true, checkedAt: "", libraries: [] };
            if (action === "start")
              return {
                id: "a".repeat(32),
                name: "fixture build",
                state: "queued",
                stage: "validate",
                startedAt: "2026-11-22T00:00:00Z",
                updatedAt: "2026-11-22T00:00:00Z",
                build: "/fixture/builds/build-1",
                folder: "/fixture/jobs/1",
                error: "",
                request: args.request,
              };
            return null;
          },
        },
      });
    },
    { packs, bootstrap },
  );
  return {
    /** Queue the path the next dialog-open will return (call after goto). */
    queue: (path: string) =>
      page.evaluate(
        (value) =>
          (
            window as unknown as { __versionPicks: string[] }
          ).__versionPicks.push(value),
        path,
      ),
    calls: () =>
      page.evaluate(
        () =>
          (
            window as unknown as {
              __versionCalls: { command: string; args: Record<string, unknown> }[];
            }
          ).__versionCalls,
      ),
  };
}

const sidebar = (page: Page) => page.locator(".sidebar-bottom");

test("an imported 1.21.1 pack shows its line and Java 21", async ({ page }) => {
  const source = "/packs/one-21.mrpack";
  const ipc = await mockDesktop(page, { [source]: packReport("1.21.1", source) });
  await page.goto("/");
  await ipc.queue(source);
  await page.getByRole("button", { name: "选择 .mrpack", exact: true }).click();
  await expect(page.getByText("静态检查通过", { exact: true })).toBeVisible();
  await expect(sidebar(page)).toContainText("Minecraft Java 1.21.1");
  await expect(sidebar(page)).toContainText("Java 21 · Fabric · iOS ARM64");
  await expect(sidebar(page)).not.toContainText("26.1.2");
});

test("switching to a 26.1.2 pack drops the 1.21.1 line", async ({ page }) => {
  const first = "/packs/one-21.mrpack";
  const second = "/packs/two-26.mrpack";
  const ipc = await mockDesktop(page, {
    [first]: packReport("1.21.1", first),
    [second]: packReport("26.1.2", second),
  });
  await page.goto("/");
  await ipc.queue(first);
  await page.getByRole("button", { name: "选择 .mrpack", exact: true }).click();
  await expect(sidebar(page)).toContainText("Minecraft Java 1.21.1");

  // Re-entering the builder forgets the pack, so the next import is clean.
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "构建 IPA", exact: true }).first().click();
  await expect(
    page.getByRole("heading", { name: "从你的整合包开始" }),
  ).toBeVisible();
  await ipc.queue(second);
  await page.getByRole("button", { name: "选择 .mrpack", exact: true }).click();
  await expect(sidebar(page)).toContainText("Minecraft Java 26.1.2");
  await expect(sidebar(page)).toContainText("Java 25 · Fabric · iOS ARM64");
  await expect(sidebar(page)).not.toContainText("1.21.1");
});

test("the vanilla dropdown offers 1.21.1 and the build requests carry it", async ({
  page,
}) => {
  const ipc = await mockDesktop(page, {});
  await page.goto("/");
  await page.getByRole("button", { name: /^原版 / }).click();
  await expect(
    page.getByRole("heading", { name: "Minecraft Java 26.1.2", exact: true }),
  ).toBeVisible();

  const trigger = page.getByRole("button", { name: "原版构建版本", exact: true });
  await trigger.click();
  await page
    .getByRole("listbox", { name: "原版构建版本", exact: true })
    .getByRole("option", { name: "Minecraft 1.21.1 · Java 21", exact: true })
    .click();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("heading", { name: "Minecraft Java 1.21.1", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("原版构建路径 · Java 21", { exact: true })).toBeVisible();

  // Straight to review; the mocked checks report nothing missing.
  await page.getByRole("button", { name: "4 确认构建", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "构建 IPA", exact: true }).last(),
  ).toBeEnabled();
  await page.getByRole("button", { name: "构建 IPA", exact: true }).last().click();

  await expect
    .poll(async () => (await ipc.calls()).filter((c) => c.command === "workbench_action" && c.args.action === "start").length)
    .toBeGreaterThan(0);
  const calls = (await ipc.calls()).filter((c) => c.command === "workbench_action");
  const startIndex = calls.findIndex((c) => c.args.action === "start");
  expect(startIndex).toBeGreaterThan(0);
  const request = calls[startIndex].args.request as { minecraftVersion?: string; loader?: string };
  expect(request.minecraftVersion).toBe("1.21.1");
  expect(request.loader).toBe("vanilla");
  // The dependency preflight right before it must ask for the same line.
  const before = calls[startIndex - 1];
  expect(before.args.action).toBe("dependencies");
  expect((before.args.request as { minecraftVersion?: string }).minecraftVersion).toBe("1.21.1");
});

test("preparing the environment after choosing 1.21.1 asks for 1.21.1", async ({
  page,
}) => {
  const ipc = await mockDesktop(page, {});
  await page.goto("/");
  await page.getByRole("button", { name: /^原版 / }).click();
  await page.getByRole("button", { name: "原版构建版本", exact: true }).click();
  await page
    .getByRole("listbox", { name: "原版构建版本", exact: true })
    .getByRole("option", { name: "Minecraft 1.21.1 · Java 21", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Minecraft Java 1.21.1", exact: true }),
  ).toBeVisible();

  // The environment page owns the one-click preparation button.
  await page.getByRole("button", { name: "2 构建环境", exact: true }).click();
  const prepare = page.getByRole("button", {
    name: /重新检查并准备|一键下载并准备/,
  });
  await expect(prepare).toBeEnabled();
  await prepare.click();

  await expect
    .poll(async () => (await ipc.calls()).filter((c) => c.command === "workbench_action" && c.args.action === "setup-start").length)
    .toBeGreaterThan(0);
  const call = (await ipc.calls()).find(
    (c) => c.command === "workbench_action" && c.args.action === "setup-start",
  )!;
  const request = call.args.request as { minecraftVersion?: string };
  // Without this the workbench falls back to the bootstrap default 26.1.2.
  expect(request.minecraftVersion).toBe("1.21.1");
});
