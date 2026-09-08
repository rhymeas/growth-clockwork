import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LessonContent, lessonMarkdown } from "./LessonContent";

describe("final editorial product", () => {
  it("extracts complete content instead of its internal envelope", () => {
    expect(lessonMarkdown(JSON.stringify({contract_id: "channel-product@1", payload: {complete_content: {markdown: "A complete lesson."}}}))).toBe("A complete lesson.");
    expect(lessonMarkdown('{"private":"data"}')).toBeNull();
  });
  it("preserves source links, claim tables, and list order without code or executable HTML", () => {
    render(<LessonContent markdown={'# Redundant title\n\n## Check the source\n\nRead [Original source](https://example.com/source).\n\n1. First\n2. Second\n\n| Claim | Finding |\n| --- | --- |\n| Speed | **Not established** |\n\n> A corrected summary.\n\n```json\n{"private":true}\n```\n<script>alert(1)</script>'} />);
    expect(screen.getByRole("heading", {name:"Check the source"})).toBeVisible();
    expect(screen.queryByRole("heading", {level:1})).toBeNull();
    expect(screen.getByRole("link", {name:"Original source"})).toHaveAttribute("href", "https://example.com/source");
    expect(screen.getByRole("table")).toHaveTextContent("Not established");
    expect(screen.queryByText(/private|alert\(1\)/)).toBeNull();
    expect(screen.getAllByRole("listitem").map(item=>item.textContent)).toEqual(["First", "Second"]);
  });
});
