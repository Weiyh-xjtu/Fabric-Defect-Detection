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

/** 分析类查询工具：结果是统计数据而非检测结果，渲染查询结果面板 */
export const QUERY_TOOLS = new Set([
  "query_detection_statistics",
  "query_detection_trends",
  "query_system_users",
  "query_system_roles",
]);

const TASK_TYPE_LABELS = {
  all: "全部",
  single: "单图",
  batch: "批量",
  video: "视频",
};

/** 把 ISO 时间戳压缩成 "YYYY-MM-DD" 便于面板展示 */
function shortDate(value) {
  if (!value || typeof value !== "string") return null;
  return value.slice(0, 10);
}

function dateRangeText(result) {
  const from = shortDate(result.from);
  const to = shortDate(result.to);
  if (!from && !to) return null;
  return `${from || "?"} ~ ${to || "?"}`;
}

/**
 * 为分析类查询工具构建查询结果面板数据。
 * 只挑与该查询相关的字段（不含推理耗时等检测执行指标）。
 * 场景与时间范围放入 context（展示在摘要行），不重复进面板字段。
 *
 * @returns {{fields: Array<{label: string, value: string|number}>, context: string}|null}
 */
export function buildQueryPanel(tool, result) {
  const fields = [];
  const push = (label, value) => {
    if (value !== undefined && value !== null && value !== "") {
      fields.push({ label, value });
    }
  };
  // 场景与时间范围只出现在摘要行，直接给值，不带标签
  const context = [result.scene, dateRangeText(result)]
    .filter(Boolean)
    .join(" · ");

  if (tool === "query_detection_statistics") {
    if (result.defect) {
      // 缺陷维度统计
      push("缺陷类别", result.defect);
      push("命中任务", result.matched_tasks ?? 0);
      push("命中图片", result.matched_images ?? 0);
      push("缺陷目标数", result.defect_count ?? 0);
    } else {
      push("任务类型", TASK_TYPE_LABELS[result.task_type] || result.task_type);
      push("任务总数", result.total_tasks ?? 0);
      push("已完成", result.completed_tasks ?? 0);
      if (result.total_tasks) push("成功率", `${result.success_rate}%`);
      push("图片总数", result.total_images ?? 0);
      push("检出目标", result.total_objects ?? 0);
      const tc = result.task_type_counts;
      if (tc && result.task_type === "all") {
        push("类型分布", `单图 ${tc.single ?? 0} · 批量 ${tc.batch ?? 0} · 视频 ${tc.video ?? 0}`);
      }
    }
  } else if (tool === "query_detection_trends") {
    push("缺陷类别", result.defect);
    const daily = Array.isArray(result.daily) ? result.daily : [];
    push("统计天数", daily.length || result.days);
    push(
      result.defect ? "缺陷检出总数" : "检出目标总数",
      daily.reduce((sum, item) => sum + (item.objects || 0), 0),
    );
    if (!result.defect) {
      push("任务总数", daily.reduce((sum, item) => sum + (item.tasks || 0), 0));
      push("缺陷类别数", result.class_distribution?.length ?? 0);
    }
  } else if (tool === "query_system_users") {
    push("用户总数", result.total ?? result.items?.length ?? 0);
    if (result.role_filter) {
      push("角色筛选", result.role_filter);
      push("筛选后数量", result.filtered_count ?? 0);
    }
  } else if (tool === "query_system_roles") {
    push("角色数量", result.roles?.length ?? 0);
  }

  return fields.length ? { fields, context } : null;
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
      query: null,
      error: String(result.error),
    };
  }
  // 分析类查询工具先于检测判断：统计结果也含 total_objects 字段，
  // 若落入下面的检测分支会被误渲染成检测结果卡片（含推理耗时等无关字段）
  if (QUERY_TOOLS.has(tool)) {
    const query = buildQueryPanel(tool, result);
    return {
      // 摘要行直接给场景与时间范围的值，与面板字段互不重复
      summary: query?.context || "",
      detection: null,
      knowledge: null,
      query,
      error: null,
    };
  }
  if (Object.prototype.hasOwnProperty.call(result, "total_objects")) {
    return {
      summary: `检出 ${result.total_objects} 个目标`,
      detection: result,
      knowledge: null,
      query: null,
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
      query: null,
      error: null,
    };
  }
  if (tool === "list_session_attachments") {
    return {
      summary: `找到 ${result.available_files ?? 0} 个可用附件`,
      detection: null,
      knowledge: null,
      query: null,
      error: null,
    };
  }
  // 无可提炼信息时不给 summary，避免与状态文本（"完成"）重复展示
  return { summary: "", detection: null, knowledge: null, query: null, error: null };
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
    if (info.query) step.query = info.query;
  }
  return info;
}
