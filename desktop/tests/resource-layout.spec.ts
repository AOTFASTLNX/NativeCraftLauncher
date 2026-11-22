import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

const report = JSON.parse(
  execFileSync("python3", [path.resolve("tests/make_fixture.py")], {
    encoding: "utf8",
  }),
);

async function openResources(page: Page) {
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles({
    name: "preflight.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(report)),
  });
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "管理整合包资源" }),
  ).toBeVisible();
}

test("retention count sits on the heading row and removed hints stay gone", async ({
  page,
}) => {
  await openResources(page);
  const meta = page.locator(".section-heading-meta");
  await expect(meta).toHaveText(/^已保留\s*\d+\s*\/\s*\d+\s*项$/);
  const heading = await page
    .getByRole("heading", { name: "管理整合包资源" })
    .boundingBox();
  const badge = await meta.boundingBox();
  expect(badge).not.toBeNull();
  // Same visual row: the count's centre lies inside the heading's line box.
  expect(badge!.y).toBeGreaterThanOrEqual(heading!.y - 4);
  expect(badge!.y + badge!.height).toBeLessThanOrEqual(
    heading!.y + heading!.height + 4,
  );
  expect(badge!.x).toBeGreaterThan(heading!.x + heading!.width);
  for (const hint of [
    "取消勾选仅影响本次构建，原整合包文件保持完整。",
    "材质包和光影包按所选文件保留；保留光影包不代表当前渲染器支持启用光影。可选 Mod 由你选择，下载后的依赖与重复版本仍需检查。",
    "必需 / 可选依据整合包清单的客户端声明；包内和本地资源未声明时列为待确认。 分级不代表 iOS 兼容性，必需项仍可手动排除。",
  ])
    await expect(page.getByText(hint, { exact: false })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "全选当前列表", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保留当前列表", exact: true }),
  ).toHaveCount(0);
});

test("retention count updates with selection and wraps on narrow screens", async ({
  page,
}) => {
  await page.setViewportSize({ width: 760, height: 900 });
  await openResources(page);
  const meta = page.locator(".section-heading-meta");
  const heading = page.getByRole("heading", { name: "管理整合包资源" });
  const before = await meta.textContent();
  const boxes = await page.evaluate(() => {
    const h = document.querySelector(".section-heading-title h2")!;
    const m = document.querySelector(".section-heading-meta")!;
    const content = document.querySelector(".step-content")!;
    return {
      heading: h.getBoundingClientRect().toJSON(),
      meta: m.getBoundingClientRect().toJSON(),
      content: content.getBoundingClientRect().toJSON(),
      overflow: content.scrollWidth,
      width: content.clientWidth,
    };
  });
  expect(boxes.meta.right).toBeLessThanOrEqual(boxes.content.right + 1);
  expect(boxes.overflow).toBeLessThanOrEqual(boxes.width + 1);
  expect(boxes.meta.top).toBeGreaterThanOrEqual(boxes.heading.top - 1);
  await page.getByRole("checkbox").first().uncheck();
  await expect(meta).not.toHaveText(before!);
  await expect(heading).toBeVisible();
});
