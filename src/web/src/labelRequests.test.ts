import { expect, it } from "vitest";
import { LabelRequestEpoch } from "./labelRequests";
it("rejects an old page after a filter change or deletion reload", async () => {
  const epoch = new LabelRequestEpoch();
  const token = epoch.capture();
  let release!: () => void;
  const delayed = new Promise<void>((r) => {
    release = r;
  });
  const visible: string[] = [];
  const request = delayed.then(() => {
    if (epoch.accepts(token)) visible.push("deleted or wrong-day evidence");
  });
  epoch.replace();
  release();
  await request;
  expect(visible).toEqual([]);
  expect(epoch.accepts(epoch.capture())).toBe(true);
});
