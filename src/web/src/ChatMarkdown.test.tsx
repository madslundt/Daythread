import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ChatMarkdown } from "./ChatMarkdown";

it("formats chat answers with headings, nested lists, quotes, tables and code", () => {
  const text = [
    "# Weekend plans", "", "First line\nSecond line", "",
    "1. **Pick a date**\n   - Ask *Sam*\n   - [x] Send a note", "", "> Make time.", "",
    "| Day | Plan |\n| --- | --- |\n| Friday | ~~Dinner~~ |", "",
    "Try `npm test`:", "", "```sh\nnpm test\n```",
  ].join("\n");
  const html = renderToStaticMarkup(<ChatMarkdown text={text} />);
  for (const fragment of ["<h3>Weekend plans</h3>", "First line<br/>", "<ol", "<ul", "<strong>Pick a date</strong>",
    "<em>Sam</em>", 'type="checkbox"', "<blockquote>", "<table>", "<th>Day</th>", "<del>Dinner</del>",
    "<code>npm test</code>", '<pre><code class="language-sh">npm test']) {
    expect(html).toContain(fragment);
  }
});

it("renders streamed Markdown without live links or keyboard stops in draft tables", () => {
  const html = renderToStaticMarkup(<ChatMarkdown draft text={"**In progress** [link](https://example.com)\n\n| A |\n| - |\n| B |"} />);
  expect(html).toContain("<strong>In progress</strong>");
  expect(html).toContain("<table>");
  expect(html).toContain('aria-live="off"');
  expect(html).not.toMatch(/href=|tabindex=/i);
});

it("does not render raw HTML, load remote images, or activate non-web URLs", () => {
  const html = renderToStaticMarkup(<ChatMarkdown text={[
    '<iframe src="https://example.com/frame"></iframe>', "",
    '![A diagram](https://example.com/image.png) [Local file](file:///notes.txt) [Docs](https://example.com/docs)',
  ].join("\n")} />);
  expect(html).toContain("<span>A diagram</span>");
  expect(html).toContain("<span>Local file</span>");
  expect(html).toContain('href="https://example.com/docs" target="_blank" rel="noopener noreferrer"');
  expect(html).not.toMatch(/<iframe|<img|src=|href="file:/);
});

it("replaces known citation groups with one button per recording group, leaving code, links and unknown ids alone", () => {
  const first = "8ab91a1a-ab55-4d7c-8e66-637ef20a1cd1";
  const second = "8ab91a1a-ab55-4d7c-8e66-637ef20a1cd2";
  const third = "8ab91a1a-ab55-4d7c-8e66-637ef20a1cd3";
  const unknown = "8ab91a1a-ab55-4d7c-8e66-637ef20a1cd4";
  const citations = new Map([[first, { number: 1, id: first }], [second, { number: 1, id: second }], [third, { number: 2, id: third }]]);
  const html = renderToStaticMarkup(<ChatMarkdown citations={citations} onCitation={() => {}}
    text={`**The plan [${first}, ${second}, ${third}]**. \`[${first}]\` [${second}](https://example.com) [${first}, ${unknown}]`} />);
  expect(html.match(/<button /g)).toHaveLength(2);
  expect(html).toContain('aria-label="Open source 1">1</button>');
  expect(html).toContain('aria-label="Open source 2">2</button>');
  expect(html).toContain(`<code>[${first}]</code>`);
  expect(html).toContain(`>${second}</a>`);
  expect(html).toContain(`[${first}, ${unknown}]`);
  const draft = renderToStaticMarkup(<ChatMarkdown draft citations={citations} onCitation={() => {}} text={`[${first}]`} />);
  expect(draft).not.toContain("<button");
});
