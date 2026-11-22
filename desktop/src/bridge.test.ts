import { beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock is hoisted above the imports, so the shared spy has to be hoisted too.
const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({
  invoke,
  isTauri: () => true,
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));

import { workbench } from "./bridge";
import { defaultForm, type BuildForm } from "./workflow";

function form(overrides: Partial<BuildForm> = {}): BuildForm {
  return {
    ...defaultForm,
    workspace: "/fixture/workspace",
    python: "/usr/bin/python3",
    minecraftVersion: "1.21.1",
    ...overrides,
  };
}

function sent(): Record<string, any> {
  expect(invoke).toHaveBeenCalledTimes(1);
  const [command, payload] = invoke.mock.calls[0];
  expect(command).toBe("workbench_action");
  return payload;
}

describe("workbench bridge", () => {
  beforeEach(() => invoke.mockReset());

  it("carries the selected line on a field-only setup-start request", () => {
    // The regression: setup-start sent only { workspace, fields }, so the
    // workbench fell back to the 26.1.2 line recorded by bootstrap and could
    // never prepare the 1.21.1 environment the user picked.
    void workbench(form(), "setup-start", undefined, { owned: true });
    const payload = sent();
    expect(payload.request.minecraftVersion).toBe("1.21.1");
    expect(payload.request.fields).toEqual({ owned: true });
    expect(payload.request.workspace).toBe("/fixture/workspace");
    expect(payload.id).toBeNull();
  });

  it("keeps a field-only request minimal instead of sending the whole form", () => {
    // Retry and other field actions must not inherit unrelated form state such
    // as quickBuild, which the workbench would treat as an explicit choice.
    void workbench(
      form({ quickBuild: true, source: "/fixture/pack.mrpack", output: "/fixture/out" }),
      "setup-start",
      undefined,
      { owned: true },
    );
    const payload = sent();
    expect(Object.keys(payload.request).sort()).toEqual([
      "fields",
      "minecraftVersion",
      "workspace",
    ]);
    expect(payload.request).not.toHaveProperty("quickBuild");
    expect(payload.request).not.toHaveProperty("source");
    expect(payload.request).not.toHaveProperty("output");
  });

  it("keeps every field verbatim so existing field semantics do not change", () => {
    const fields = { owned: true, keepBuilds: 2, groups: ["cache"], nested: { a: 1 } };
    void workbench(form(), "setup-start", undefined, fields);
    expect(sent().request.fields).toEqual(fields);
  });

  it("leaves retry on the whole-form path, including its quickBuild choice", () => {
    // Retry is called with the recorded request as the form and no fields.
    void workbench(
      form({ ...{ quickBuild: true }, source: "/fixture/pack.mrpack" }),
      "retry",
      "a".repeat(32),
    );
    const payload = sent();
    expect(payload.request.minecraftVersion).toBe("1.21.1");
    expect(payload.request.quickBuild).toBe(true);
    expect(payload.request).not.toHaveProperty("fields");
    expect(payload.id).toBe("a".repeat(32));
  });

  it("carries the selected line for cancel without inventing fields", () => {
    void workbench(form(), "cancel", "b".repeat(32));
    const payload = sent();
    expect(payload.request.minecraftVersion).toBe("1.21.1");
    expect(payload.request).not.toHaveProperty("fields");
    expect(payload.id).toBe("b".repeat(32));
  });

  it("sends the whole form for actions that already carried it", () => {
    void workbench(form({ source: "/fixture/pack.mrpack", loader: "fabric" }), "start");
    const payload = sent();
    expect(payload.request.minecraftVersion).toBe("1.21.1");
    expect(payload.request.source).toBe("/fixture/pack.mrpack");
    expect(payload.request.loader).toBe("fabric");
    expect(payload.request).not.toHaveProperty("fields");
  });

  it("does not let a missing field map become an empty object", () => {
    void workbench(form(), "history");
    expect(sent().request).not.toHaveProperty("fields");
  });

  it("preserves the default line when the form still holds it", () => {
    void workbench(form({ minecraftVersion: "26.1.2" }), "setup-start", undefined, { owned: true });
    expect(sent().request.minecraftVersion).toBe("26.1.2");
  });
});
