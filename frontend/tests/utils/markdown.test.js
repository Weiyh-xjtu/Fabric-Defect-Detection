import { describe, expect, it } from "vitest";
import { renderMarkdown, splitAgentSections } from "@/utils/markdown";

describe("splitAgentSections 专家分节拆分", () => {
  it("无分节标题时返回单个 agent 为 null 的片段", () => {
    const secs = splitAgentSections("你好，有什么可以帮你？");
    expect(secs).toEqual([{ agent: null, content: "你好，有什么可以帮你？" }]);
  });

  it("单分节标题拆出对应专家", () => {
    const secs = splitAgentSections("#### 🔍 检测专家\n\n检测到目标总数：1");
    expect(secs).toHaveLength(1);
    expect(secs[0].agent).toBe("detection");
    expect(secs[0].content).toContain("检测到目标总数");
    expect(secs[0].content).not.toContain("检测专家");
  });

  it("多分节按顺序拆分三位专家，分割线留在上一节末尾", () => {
    const secs = splitAgentSections(
      "#### 🔍 检测专家\n\nA\n\n---\n\n#### 📊 数据分析\n\nB\n\n---\n\n#### 📖 知识问答\n\nC",
    );
    expect(secs.map((s) => s.agent)).toEqual(["detection", "analysis", "qa"]);
    expect(secs[0].content).toContain("A");
    expect(secs[0].content).toMatch(/---$/);
    expect(secs[1].content).toContain("B");
    expect(secs[2].content).toBe("C");
  });

  it("正文中的普通 h4 不触发拆分", () => {
    const secs = splitAgentSections("#### 训练结果\n\n完成");
    expect(secs).toHaveLength(1);
    expect(secs[0].agent).toBeNull();
  });
});

describe("renderMarkdown", () => {
  it("渲染基本 markdown", () => {
    expect(renderMarkdown("**加粗**")).toContain("<strong>加粗</strong>");
  });
});
