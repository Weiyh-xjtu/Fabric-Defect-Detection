/**
 * 工具调用链状态管理。
 *
 * 一条 AI 消息可能触发多次工具调用（例如：查会话附件 → 批量检测，
 * 或知识库检索）。这里把 SSE 的 tool_call / tool_result 事件归并为
 * 一个有序步骤数组，并解析出结构化结果供界面渲染。
 */
import { TOOL_NAME_MAP } from "@/utils/stream";

export const AGENT_NAME_MAP = {
  detection: "检测专家",
  analysis: "数据分析",
  qa: "知识问答",
};

export function toolDisplayName(tool) {
  return TOOL_NAME_MAP[tool] || tool;
}

/** 记录一次工具调用开始，追加进行中的步骤（保留事件中的专家归属） */
export function beginToolStep(chain, event) {
  chain.push({
    tool: event.tool,
    input: event.input,
    agent: event.agent,
    status: "running",
    summary: "",
  });
}

/**
 * 解析工具结果字符串，返回结构化信息。
 *
 * @returns {{summary: string, detection: object|null, knowledge: object|null, error: string|null}}
 */
export function parseToolResult(tool, rawResult) {
  let result;
  try {
    result = JSON.parse(rawResult);
  } catch {
    return { summary: "", detection: null, knowledge: null, error: null };
  }
  if (!result || typeof result !== "object") {
    return { summary: "", detection: null, knowledge: null, error: null };
  }
  if (result.error) {
    return {
      summary: String(result.error),
      detection: null,
      knowledge: null,
      error: String(result.error),
    };
  }
  if (Object.prototype.hasOwnProperty.call(result, "total_objects")) {
    return {
      summary: `检出 ${result.total_objects} 个目标`,
      detection: result,
      knowledge: null,
      error: null,
    };
  }
  if (tool === "search_knowledge") {
    const count = result.results?.length ?? 0;
    const mode = result.retrieval_mode === "pgvector" ? "向量检索" : "词法检索";
    return {
      summary: `${mode} · 命中 ${count} 条片段`,
      detection: null,
      knowledge: result,
      error: null,
    };
  }
  if (tool === "list_session_attachments") {
    return {
      summary: `找到 ${result.available_files ?? 0} 个可用附件`,
      detection: null,
      knowledge: null,
      error: null,
    };
  }
  return { summary: "完成", detection: null, knowledge: null, error: null };
}

/**
 * 记录一次工具调用结束：定位对应的进行中步骤并写入状态与结果。
 * 匹配优先级：同工具+同专家 → 同工具 → 任意进行中步骤。
 * 并行多专家时用 agent 消歧；旧事件无 agent 字段时退回按工具名匹配。
 */
export function completeToolStep(chain, event) {
  const info = parseToolResult(event.tool, event.result);
  const reversed = [...chain].reverse();
  const step =
    reversed.find(
      (item) =>
        item.status === "running" &&
        item.tool === event.tool &&
        (!event.agent || !item.agent || item.agent === event.agent),
    ) ||
    reversed.find((item) => item.status === "running" && item.tool === event.tool) ||
    reversed.find((item) => item.status === "running");
  if (step) {
    step.status = info.error ? "error" : "done";
    step.summary = info.summary;
    if (info.knowledge) step.knowledge = info.knowledge;
  }
  return info;
}
