import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";
import express from "express";
import dotenv from "dotenv";
import makeWASocket, {
  areJidsSameUser,
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  getContentType,
  jidNormalizedUser,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import qrcode from "qrcode-terminal";
import QRCode from "qrcode";
import OpenAI from "openai";

dotenv.config();

const REQUIRED_ENV = ["OPENAI_API_KEY"];
const LLM_BACKEND = (process.env.LLM_BACKEND || "codex").toLowerCase();
if (LLM_BACKEND === "openai") {
  for (const key of REQUIRED_ENV) {
    if (!process.env[key]) {
      console.error(`Missing required env var: ${key}`);
      process.exit(1);
    }
  }
}

const PORT = Number(process.env.PORT || "8787");
const AUTH_DIR = path.resolve(process.env.AUTH_DIR || "./data/auth");
const STATE_DIR = path.resolve(process.env.STATE_DIR || "./data/state");
const HISTORY_LIMIT = Number(process.env.MESSAGE_HISTORY_LIMIT || "20");
const PROMPT_HISTORY_LIMIT = Number(process.env.PROMPT_HISTORY_LIMIT || "8");
const PROMPT_MESSAGE_CHAR_LIMIT = Number(process.env.PROMPT_MESSAGE_CHAR_LIMIT || "600");
const SYSTEM_PROMPT =
  process.env.SYSTEM_PROMPT ||
  "You are a concise and helpful assistant replying over WhatsApp.";
const OPENAI_BASE_URL = process.env.OPENAI_BASE_URL || "";
const GROUP_SYSTEM_PROMPT =
  process.env.GROUP_SYSTEM_PROMPT ||
  "你是 WhatsApp 群里的成人搞笑搭子，只在被 /ai 明确召唤时回复。你只回答群里的消息，不执行任何操作。可以接梗、吐槽、写短段子和轻度成人幽默；回复短一点，像群友聊天，不端着。边界：不主动刷屏，不攻击群友真实身份，不做人身羞辱；不写涉及未成年人、非自愿、胁迫、偷拍、性暴力或仇恨歧视的内容；遇到过界请求就把梗转成安全的成人幽默。";
const GROUP_AI_NAME = process.env.GROUP_AI_NAME || "酒浅义深";
const CODEX_CLI_PATH = process.env.CODEX_CLI_PATH || "codex";
const CODEX_MODEL = process.env.CODEX_MODEL || "";
const CODEX_WORKDIR = path.resolve(process.env.CODEX_WORKDIR || process.cwd());
const CODEX_REASONING_EFFORT = process.env.CODEX_REASONING_EFFORT || "medium";
const CODEX_TIMEOUT_MS = Number(process.env.CODEX_TIMEOUT_MS || "120000");
const CODEX_EXEC_ENV = (process.env.CODEX_EXEC_ENV || "host").toLowerCase();
const INPUT_DEBOUNCE_MS = Number(process.env.INPUT_DEBOUNCE_MS || "2500");
const ACK_DELAY_MS = Number(process.env.ACK_DELAY_MS || "4000");
const PROGRESS_INTERVAL_MS = Number(process.env.PROGRESS_INTERVAL_MS || "25000");
const ALLOWED_GROUP_JIDS = new Set(
  (process.env.ALLOWED_GROUP_JIDS || "")
    .split(",")
    .map((value) => jidNormalizedUser(value.trim()))
    .filter(Boolean)
);
const GROUP_TRIGGER_PATTERN = process.env.GROUP_TRIGGER_PATTERN || "^/ai\\s+";
let groupTriggerRegex;
try {
  groupTriggerRegex = new RegExp(GROUP_TRIGGER_PATTERN, "i");
} catch (error) {
  console.error(`Invalid GROUP_TRIGGER_PATTERN: ${GROUP_TRIGGER_PATTERN}`, error);
  process.exit(1);
}

fs.mkdirSync(AUTH_DIR, { recursive: true });
fs.mkdirSync(STATE_DIR, { recursive: true });

const client =
  LLM_BACKEND === "openai"
    ? new OpenAI({
        apiKey: process.env.OPENAI_API_KEY,
        baseURL: OPENAI_BASE_URL || undefined,
      })
    : null;

let sock;
let connectionState = "starting";
let lastQr = null;
let linkedJid = null;
let isShuttingDown = false;
const chatRuntimes = new Map();
const recentInteractions = [];
const QR_TXT_FILE = path.join(STATE_DIR, "last-qr.txt");
const QR_SVG_FILE = path.join(STATE_DIR, "last-qr.svg");
const ENV_FILE = path.resolve(".env");

const normalizeNumber = (jid = "") => jid.split("@")[0] || jid;
const normalizedLinkedUserJid = () => jidNormalizedUser(linkedJid || "");
const normalizedLinkedLid = () => jidNormalizedUser(sock?.user?.lid || "");
const isGroupJid = (jid = "") => jid.endsWith("@g.us");

const parseEnvFile = () => {
  if (!fs.existsSync(ENV_FILE)) {
    return {};
  }
  const parsed = {};
  for (const line of fs.readFileSync(ENV_FILE, "utf8").split(/\r?\n/)) {
    if (!line || line.trim().startsWith("#") || !line.includes("=")) {
      continue;
    }
    const index = line.indexOf("=");
    parsed[line.slice(0, index)] = line.slice(index + 1);
  }
  return parsed;
};

const setEnvValues = (updates) => {
  let text = fs.existsSync(ENV_FILE) ? fs.readFileSync(ENV_FILE, "utf8") : "";
  for (const [key, value] of Object.entries(updates)) {
    const line = `${key}=${String(value ?? "")}`;
    const pattern = new RegExp(`^${key}=.*$`, "m");
    if (pattern.test(text)) {
      text = text.replace(pattern, line);
    } else {
      text += `${text.endsWith("\n") || !text ? "" : "\n"}${line}\n`;
    }
  }
  fs.writeFileSync(ENV_FILE, text);
};

const maskSecret = (value = "") => {
  if (!value) {
    return "";
  }
  if (value.length <= 8) {
    return "********";
  }
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
};

const getSetupSnapshot = () => {
  const env = parseEnvFile();
  return {
    status: "ok",
    current: {
      llmBackend: LLM_BACKEND,
      openaiModel: process.env.OPENAI_MODEL || "gpt-5.2",
      openaiBaseUrl: OPENAI_BASE_URL,
      openaiApiKeyMasked: maskSecret(process.env.OPENAI_API_KEY || ""),
      codexCliPath: CODEX_CLI_PATH,
      codexModel: CODEX_MODEL,
      codexWorkdir: CODEX_WORKDIR,
      codexExecEnv: CODEX_EXEC_ENV,
      codexReasoningEffort: CODEX_REASONING_EFFORT,
      codexTimeoutMs: CODEX_TIMEOUT_MS,
    },
    env: {
      llmBackend: env.LLM_BACKEND || "",
      openaiModel: env.OPENAI_MODEL || "",
      openaiBaseUrl: env.OPENAI_BASE_URL || "",
      openaiApiKeyMasked: maskSecret(env.OPENAI_API_KEY || ""),
      codexCliPath: env.CODEX_CLI_PATH || "",
      codexModel: env.CODEX_MODEL || "",
      codexWorkdir: env.CODEX_WORKDIR || "",
      codexExecEnv: env.CODEX_EXEC_ENV || "",
      codexReasoningEffort: env.CODEX_REASONING_EFFORT || "",
      codexTimeoutMs: env.CODEX_TIMEOUT_MS || "",
    },
    providers: [
      {
        id: "codex",
        name: "Codex CLI",
        mode: "local",
        description: "使用本机 Codex CLI。适合当前桥接器需要本机上下文的场景。",
      },
      {
        id: "openai",
        name: "OpenAI / OpenAI-compatible",
        mode: "api",
        description: "使用 OpenAI SDK，可填写 Base URL 对接兼容 OpenAI Chat/Responses 风格的网关。",
      },
    ],
    restartRequiredForSavedChanges: true,
  };
};

const chatFilePath = (chatId) => {
  const safe = chatId.replace(/[^a-zA-Z0-9_.-]/g, "_");
  return path.join(STATE_DIR, `${safe}.json`);
};

const loadChatHistory = (chatId) => {
  const file = chatFilePath(chatId);
  if (!fs.existsSync(file)) {
    return [];
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const saveChatHistory = (chatId, messages) => {
  const file = chatFilePath(chatId);
  fs.writeFileSync(file, JSON.stringify(messages.slice(-HISTORY_LIMIT), null, 2));
};

const resetChatHistory = (chatId) => {
  const file = chatFilePath(chatId);
  if (fs.existsSync(file)) {
    fs.unlinkSync(file);
  }
};

const appendChatMessage = (chatId, role, content) => {
  const history = loadChatHistory(chatId);
  history.push({ role, content, createdAt: new Date().toISOString() });
  saveChatHistory(chatId, history);
  return history;
};

const getChatRuntime = (chatId) => {
  if (!chatRuntimes.has(chatId)) {
    chatRuntimes.set(chatId, {
      timer: null,
      running: false,
      pending: false,
      sequence: 0,
      lastStatusAt: 0,
      answerOnly: false,
      number: null,
      lastText: "",
      lastReply: "",
      lastError: "",
      receivedAt: null,
      startedAt: null,
      completedAt: null,
    });
  }
  return chatRuntimes.get(chatId);
};

const serializeRuntime = (chatId, runtime) => ({
  chatId,
  number: runtime.number,
  mode: runtime.answerOnly ? "answer-only" : "assistant",
  running: Boolean(runtime.running),
  pending: Boolean(runtime.pending),
  sequence: runtime.sequence,
  lastText: runtime.lastText,
  lastReply: runtime.lastReply,
  lastError: runtime.lastError,
  receivedAt: runtime.receivedAt,
  startedAt: runtime.startedAt,
  completedAt: runtime.completedAt,
});

const upsertRecentInteraction = (interaction) => {
  const existingIndex = recentInteractions.findIndex(
    (item) => item.chatId === interaction.chatId && item.sequence === interaction.sequence
  );
  if (existingIndex >= 0) {
    recentInteractions[existingIndex] = {
      ...recentInteractions[existingIndex],
      ...interaction,
    };
  } else {
    recentInteractions.unshift(interaction);
  }
  recentInteractions.sort((a, b) =>
    String(b.receivedAt || "").localeCompare(String(a.receivedAt || ""))
  );
  recentInteractions.splice(10);
};

const getTaskSnapshot = () =>
  recentInteractions.length
    ? recentInteractions.slice(0, 10)
    : Array.from(chatRuntimes.entries())
        .map(([chatId, runtime]) => serializeRuntime(chatId, runtime))
        .sort((a, b) => String(b.receivedAt || "").localeCompare(String(a.receivedAt || "")))
        .slice(0, 10);

const getAllowedGroupConfigSnapshot = async () => {
  const groupsById = new Map();
  if (sock && connectionState === "open") {
    try {
      const groups = await sock.groupFetchAllParticipating();
      for (const group of Object.values(groups)) {
        groupsById.set(jidNormalizedUser(group.id), group);
      }
    } catch (error) {
      console.error("failed to fetch group names for config snapshot:", error);
    }
  }

  return Array.from(ALLOWED_GROUP_JIDS).map((jid) => {
    const group = groupsById.get(jid);
    return {
      id: jid,
      subject: group?.subject || null,
      aiName: GROUP_AI_NAME,
      triggerPattern: GROUP_TRIGGER_PATTERN,
      mode: "answer-only",
      rolePrompt: GROUP_SYSTEM_PROMPT,
      taskDefinition: "只回答群里用触发词明确召唤的消息，不执行任何操作。",
      restrictions: [
        "不运行命令",
        "不读写文件",
        "不启动或停止服务",
        "不查日志或系统",
        "不审批工单",
        "不发送通知",
        "不替用户做决定",
      ],
    };
  });
};

const buildOpenAIInput = (history) =>
  history.map((message) => ({
    role: message.role,
    content: message.content,
  }));

const compactMessageContent = (content) => {
  const text = String(content || "").replace(/\s+/g, " ").trim();
  if (text.length <= PROMPT_MESSAGE_CHAR_LIMIT) {
    return text;
  }
  return `${text.slice(0, PROMPT_MESSAGE_CHAR_LIMIT)}...`;
};

const getLatestUserText = (history) => {
  for (let index = history.length - 1; index >= 0; index -= 1) {
    if (history[index]?.role === "user") {
      return String(history[index].content || "");
    }
  }
  return "";
};

const shouldSendProgressUpdates = (history) => {
  const latestUserText = getLatestUserText(history).trim();
  if (!latestUserText) {
    return false;
  }
  if (latestUserText.length >= 120) {
    return true;
  }
  return /查|检查|搜索|运行|执行|启动|停止|停掉|重启|设置|创建|删除|修改|修复|部署|测试|分析|总结|排查|日志|工单|审批|ticket|swp|jira|github|server|service|log|deploy|restart/i.test(
    latestUserText
  );
};

const isOperationRequest = (text = "") =>
  /查|检查|搜索|运行|执行|启动|停止|停掉|重启|设置|创建|删除|修改|修复|部署|测试|排查|日志|工单|审批|批准|拒绝|提交|发送|下载|上传|安装|卸载|ticket|swp|jira|github|server|service|log|deploy|restart|approve|reject|delete|create|update|install|run|execute/i.test(
    text
  );

const buildCapabilityHints = (history) => {
  const latestUserText = getLatestUserText(history);
  const text = latestUserText.toLowerCase();
  const hints = [];

  hints.push("You are Codex running on the local machine, not a generic chat bot.");
  hints.push("Prefer using local skills, plugins, and shell tools that are already installed before saying a capability is unavailable.");
  hints.push("Reply in Chinese by default unless the user clearly asks for another language.");

  if (
    /fw|firewall|工单|审批|待审批|pending|swp|skill|skills|插件/.test(text)
  ) {
    hints.push("The local environment includes the skill `fw-ticket-review-summary` at `/Users/zhangwei/.codex/skills/fw-ticket-review-summary/SKILL.md`.");
    hints.push("The local environment includes the plugin skill `smc-operations:swp` at `/Users/zhangwei/.codex/plugins/cache/smc-marketplace/smc-operations/1.0.0/skills/swp/SKILL.md`.");
    hints.push("When the user asks to check pending approvals or FW tickets, inspect those skill files first if needed, then use the `swp` CLI instead of asking for manual ticket IDs.");
    hints.push("For FW pending approvals, start from `swp list --json`, identify firewall tickets, fetch details with `swp get`, and summarize them in the review-table format from `fw-ticket-review-summary`.");
    hints.push("Do not claim SWP access is unavailable until you have actually checked whether `swp` exists and whether authentication can proceed.");
    hints.push("If `swp` exists but the query fails, distinguish missing tool vs authentication vs DNS/network failure explicitly.");
  }

  return hints;
};

const buildCodexPrompt = (history) => {
  const promptHistory = history.slice(-PROMPT_HISTORY_LIMIT);
  const lines = [];
  lines.push(SYSTEM_PROMPT);
  lines.push("");
  lines.push("Reply as the same assistant in this WhatsApp chat.");
  lines.push("Be concise and helpful.");
  lines.push("For simple questions, answer directly without progress acknowledgements.");
  lines.push("Only mention ongoing work when the user asked for a slow or multi-step task.");
  lines.push("");
  lines.push("Execution guidance:");
  for (const hint of buildCapabilityHints(promptHistory)) {
    lines.push(`- ${hint}`);
  }
  lines.push("");
  lines.push("Conversation history:");
  for (const message of promptHistory) {
    lines.push(`${message.role.toUpperCase()}: ${compactMessageContent(message.content)}`);
  }
  lines.push("");
  lines.push("Return only the assistant reply text.");
  return lines.join("\n");
};

const buildAnswerOnlyPrompt = (history) => {
  const promptHistory = history.slice(-PROMPT_HISTORY_LIMIT);
  const lines = [];
  lines.push(GROUP_SYSTEM_PROMPT);
  lines.push("");
  lines.push("Strict mode:");
  lines.push("- You are in a WhatsApp group answer-only mode.");
  lines.push("- Do not perform, claim to perform, or imply you performed any real-world, local-machine, account, file, network, ticket, approval, deployment, or messaging operation.");
  lines.push("- Do not ask to run tools. Do not mention local files, logs, shell commands, APIs, credentials, or system access as something you can use.");
  lines.push("- If asked to operate, answer conversationally without exposing these internal restrictions.");
  lines.push("- Return only the group chat reply text.");
  lines.push("");
  lines.push("Conversation history:");
  for (const message of promptHistory) {
    lines.push(`${message.role.toUpperCase()}: ${compactMessageContent(message.content)}`);
  }
  return lines.join("\n");
};

const generateReplyWithCodex = async (history, options = {}) => {
  const outputFile = path.join(STATE_DIR, `codex-last-message-${Date.now()}.txt`);
  const args = ["exec", "--skip-git-repo-check", "--ephemeral"];
  if (options.answerOnly) {
    args.push("--sandbox", "read-only");
  } else if (CODEX_EXEC_ENV === "host") {
    args.push("--dangerously-bypass-approvals-and-sandbox");
  } else {
    args.push("--sandbox", "read-only");
  }
  args.push(
    "--color",
    "never",
    "--config",
    `reasoning_effort="${CODEX_REASONING_EFFORT}"`,
    "--output-last-message",
    outputFile,
    "-C",
    CODEX_WORKDIR
  );
  if (CODEX_MODEL) {
    args.push("--model", CODEX_MODEL);
  }
  args.push("-");
  const prompt = options.answerOnly ? buildAnswerOnlyPrompt(history) : buildCodexPrompt(history);
  try {
    await new Promise((resolve, reject) => {
      const child = spawn(CODEX_CLI_PATH, args, {
        cwd: CODEX_WORKDIR,
        stdio: ["pipe", "pipe", "pipe"],
      });
      let stderr = "";
      let stdout = "";
      const timer = setTimeout(() => {
        child.kill("SIGTERM");
        reject(new Error("codex exec timed out"));
      }, CODEX_TIMEOUT_MS);

      child.stdout.on("data", (chunk) => {
        stdout += String(chunk);
      });
      child.stderr.on("data", (chunk) => {
        stderr += String(chunk);
      });
      child.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.on("close", (code) => {
        clearTimeout(timer);
        if (code === 0) {
          resolve();
          return;
        }
        reject(
          new Error(
            `codex exec failed with code ${code}: ${stderr || stdout || "unknown error"}`
          )
        );
      });

      child.stdin.write(prompt);
      child.stdin.end();
    });
    const text = fs.readFileSync(outputFile, "utf8").trim();
    return text || "I could not generate a reply just now. Please try again.";
  } finally {
    if (fs.existsSync(outputFile)) {
      fs.rmSync(outputFile, { force: true });
    }
  }
};

const generateReply = async (chatId, options = {}) => {
  const history = loadChatHistory(chatId).slice(-HISTORY_LIMIT);
  if (options.answerOnly && isOperationRequest(getLatestUserText(history))) {
    return "这个我没法直接弄，你把要点丢出来，我可以帮你嘴上参谋一下。";
  }
  if (LLM_BACKEND === "codex") {
    return generateReplyWithCodex(history, options);
  }
  const response = await client.responses.create({
    model: process.env.OPENAI_MODEL || "gpt-5.2",
    instructions: options.answerOnly ? GROUP_SYSTEM_PROMPT : SYSTEM_PROMPT,
    input: buildOpenAIInput(history),
  });
  const text = (response.output_text || "").trim();
  return text || "I could not generate a reply just now. Please try again.";
};

const splitMessage = (text, limit = 1500) => {
  if (text.length <= limit) {
    return [text];
  }
  const chunks = [];
  let remaining = text;
  while (remaining.length > limit) {
    let cut = remaining.lastIndexOf("\n", limit);
    if (cut < limit * 0.5) {
      cut = remaining.lastIndexOf(" ", limit);
    }
    if (cut < limit * 0.5) {
      cut = limit;
    }
    chunks.push(remaining.slice(0, cut).trim());
    remaining = remaining.slice(cut).trim();
  }
  if (remaining) {
    chunks.push(remaining);
  }
  return chunks;
};

const persistQrArtifacts = async (qr) => {
  qrcode.generate(qr, { small: true }, (ascii) => {
    if (ascii) {
      fs.writeFileSync(QR_TXT_FILE, ascii);
    }
  });
  const svg = await QRCode.toString(qr, { type: "svg", margin: 1, width: 300 });
  fs.writeFileSync(QR_SVG_FILE, svg);
};

const sendText = async (jid, text) => {
  const chunks = splitMessage(text);
  for (const chunk of chunks) {
    const result = await sock.sendMessage(jid, { text: chunk });
    console.log("message sent", {
      to: jid,
      id: result?.key?.id || null,
      remoteJid: result?.key?.remoteJid || null,
    });
  }
};

const maybeSendStatus = async (chatId, text) => {
  const runtime = getChatRuntime(chatId);
  const now = Date.now();
  if (now - runtime.lastStatusAt < 3000) {
    return;
  }
  runtime.lastStatusAt = now;
  await sendText(chatId, text);
};

const clearChatTimer = (runtime) => {
  if (runtime.timer) {
    clearTimeout(runtime.timer);
    runtime.timer = null;
  }
};

const processChatQueue = async (chatId, number) => {
  const runtime = getChatRuntime(chatId);
  if (runtime.running) {
    runtime.pending = true;
    return;
  }

  runtime.running = true;
  runtime.startedAt = new Date().toISOString();
  runtime.completedAt = null;
  runtime.lastError = "";
  clearChatTimer(runtime);

  try {
    while (true) {
      runtime.pending = false;
      const startedSequence = runtime.sequence;
      const answerOnly = Boolean(runtime.answerOnly);
      const sendProgressUpdates =
        !answerOnly && shouldSendProgressUpdates(loadChatHistory(chatId));
      let ackSent = false;
      const ackTimer = sendProgressUpdates
        ? setTimeout(async () => {
            try {
              ackSent = true;
              await maybeSendStatus(chatId, "已收到，正在处理中。我会继续回报进度。");
            } catch (error) {
              console.error(`failed to send ack to ${number}:`, error);
            }
          }, ACK_DELAY_MS)
        : null;

      const progressTimer = sendProgressUpdates
        ? setInterval(async () => {
            try {
              await maybeSendStatus(chatId, "还在处理，当前在本机执行命令或查数据。");
            } catch (error) {
              console.error(`failed to send progress update to ${number}:`, error);
            }
          }, PROGRESS_INTERVAL_MS)
        : null;

      try {
        const reply = await generateReply(chatId, { answerOnly });
        if (ackTimer) {
          clearTimeout(ackTimer);
        }
        if (progressTimer) {
          clearInterval(progressTimer);
        }

        if (runtime.sequence !== startedSequence || runtime.pending) {
          console.log(`discarded stale reply for ${number}`, {
            startedSequence,
            currentSequence: runtime.sequence,
          });
          continue;
        }

        appendChatMessage(chatId, "assistant", reply);
        runtime.lastReply = reply;
        runtime.completedAt = new Date().toISOString();
        upsertRecentInteraction({
          ...serializeRuntime(chatId, runtime),
          status: "completed",
        });
        await sendText(chatId, reply);
        console.log(`replied to ${number}`, { ackSent });
      } catch (error) {
        if (ackTimer) {
          clearTimeout(ackTimer);
        }
        if (progressTimer) {
          clearInterval(progressTimer);
        }
        console.error(`failed to handle message from ${number}:`, error);
        runtime.lastError = String(error?.message || error || "unknown error");
        runtime.completedAt = new Date().toISOString();
        upsertRecentInteraction({
          ...serializeRuntime(chatId, runtime),
          status: "error",
        });
        await sendText(
          chatId,
          "我这边处理超时了。你刚发的内容我收到了；我已经切到更稳的模式。你可以再发一次，或者拆成更短一句。"
        );
      }

      if (!runtime.pending) {
        break;
      }
    }
  } finally {
    runtime.running = false;
  }
};

const scheduleChatProcessing = (chatId, number, options = {}) => {
  const runtime = getChatRuntime(chatId);
  runtime.pending = true;
  runtime.answerOnly = Boolean(options.answerOnly);
  runtime.number = number;
  clearChatTimer(runtime);
  runtime.timer = setTimeout(() => {
    processChatQueue(chatId, number).catch((error) => {
      console.error(`chat queue crashed for ${number}:`, error);
    });
  }, INPUT_DEBOUNCE_MS);
};

const handleIncomingText = async (message, textOverride = null, options = {}) => {
  const chatId = message.key.remoteJid;
  const sender = message.key.participant || message.key.remoteJid;
  const number = normalizeNumber(sender);
  const body =
    textOverride ??
    message.message?.conversation ??
    message.message?.extendedTextMessage?.text ??
    "";
  const text = body.trim();
  if (!chatId || !text) {
    return;
  }

  if (!options.answerOnly && /^\/?reset$/i.test(text)) {
    resetChatHistory(chatId);
    await sendText(chatId, "Conversation reset.");
    return;
  }

  appendChatMessage(chatId, "user", text);
  const runtime = getChatRuntime(chatId);
  runtime.sequence += 1;
  runtime.number = number;
  runtime.lastText = text;
  runtime.receivedAt = new Date().toISOString();
  runtime.completedAt = null;
  runtime.lastError = "";
  upsertRecentInteraction({
    ...serializeRuntime(chatId, runtime),
    status: "queued",
  });
  scheduleChatProcessing(chatId, number, { answerOnly: options.answerOnly });
};

const startSocket = async () => {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    browser: Browsers.macOS("OpenAI WhatsApp Bridge"),
    printQRInTerminal: false,
    syncFullHistory: false,
    markOnlineOnConnect: true,
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      lastQr = qr;
      await persistQrArtifacts(qr);
      console.log("Scan this QR code with WhatsApp:");
      qrcode.generate(qr, { small: true });
    }
    if (connection) {
      connectionState = connection;
      console.log(`connection state: ${connection}`);
    }
    if (connection === "open") {
      linkedJid = sock.user?.id || null;
      lastQr = null;
      if (fs.existsSync(QR_TXT_FILE)) {
        fs.rmSync(QR_TXT_FILE, { force: true });
      }
      if (fs.existsSync(QR_SVG_FILE)) {
        fs.rmSync(QR_SVG_FILE, { force: true });
      }
      console.log(`linked as ${linkedJid}`);
    }
    if (connection === "close") {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect =
        !isShuttingDown && statusCode !== DisconnectReason.loggedOut;
      console.error("connection closed", {
        statusCode,
        shouldReconnect,
      });
      if (shouldReconnect) {
        await startSocket();
      }
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") {
      return;
    }
    for (const message of messages) {
      if (!message.message) {
        continue;
      }
      const remoteJid = jidNormalizedUser(message.key.remoteJid || "");
      const selfJid = normalizedLinkedUserJid();
      const selfLid = normalizedLinkedLid();
      const isSelfChat = Boolean(
        remoteJid &&
          ((selfJid && areJidsSameUser(remoteJid, selfJid)) ||
            (selfLid && areJidsSameUser(remoteJid, selfLid)))
      );
      const messageText =
        message.message?.conversation ||
        message.message?.extendedTextMessage?.text ||
        "";
      const isAllowedGroupMessage = Boolean(
        isGroupJid(remoteJid) &&
          ALLOWED_GROUP_JIDS.has(remoteJid) &&
          groupTriggerRegex.test(messageText)
      );
      console.log("messages.upsert item", {
        remoteJid: message.key.remoteJid || null,
        normalizedRemoteJid: remoteJid || null,
        fromMe: Boolean(message.key.fromMe),
        isSelfChat,
        isAllowedGroupMessage,
        selfJid: selfJid || null,
        selfLid: selfLid || null,
        hasMessage: Boolean(message.message),
        contentType: getContentType(message.message) || null,
        textPreview: String(messageText).slice(0, 80),
      });
      if (!isSelfChat && !isAllowedGroupMessage) {
        continue;
      }
      const contentType = getContentType(message.message);
      if (!contentType || !["conversation", "extendedTextMessage"].includes(contentType)) {
        continue;
      }
      const textOverride = isAllowedGroupMessage
        ? messageText.replace(groupTriggerRegex, "").trim()
        : null;
      await handleIncomingText(message, textOverride, {
        answerOnly: isAllowedGroupMessage,
      });
    }
  });
};

const app = express();
app.use(express.json());

app.get("/", (_req, res) => {
  res.type("html").send(`<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WhatsApp Bridge Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f5f7;
      --panel: rgba(255, 255, 255, 0.78);
      --panel-strong: rgba(255, 255, 255, 0.92);
      --text: #1d1d1f;
      --muted: #6e6e73;
      --line: rgba(29, 29, 31, 0.12);
      --line-strong: rgba(29, 29, 31, 0.18);
      --ok: #007a5a;
      --warn: #b45b00;
      --bad: #c41e3a;
      --blue: #0066cc;
      --violet: #7e3ff2;
      --shadow: 0 18px 55px rgba(0, 0, 0, 0.08);
      --font-ui: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", sans-serif;
      --font-display: "Songti SC", "STSong", "New York", "Times New Roman", serif;
      --font-cn: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      --font-literary: "Kaiti SC", "STKaiti", "Songti SC", serif;
      --font-mono: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    * { box-sizing: border-box; }
    a { color: inherit; text-decoration: none; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(135deg, rgba(0, 102, 204, 0.10) 0%, rgba(255,255,255,0) 34%),
        linear-gradient(225deg, rgba(126, 63, 242, 0.08) 0%, rgba(255,255,255,0) 32%),
        linear-gradient(180deg, #fbfbfd 0%, var(--bg) 46%, #ffffff 100%);
      color: var(--text);
      font-family: var(--font-ui);
      font-size: 14px;
      letter-spacing: 0;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 30;
      border-bottom: 1px solid var(--line);
      background: rgba(251, 251, 253, 0.72);
      backdrop-filter: blur(22px) saturate(180%);
      -webkit-backdrop-filter: blur(22px) saturate(180%);
    }
    .nav {
      max-width: 1180px;
      margin: 0 auto;
      padding: 14px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .mark {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: conic-gradient(from 160deg, #1d1d1f, #0066cc, #20c997, #1d1d1f);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.38), 0 8px 18px rgba(0, 102, 204, 0.22);
    }
    .brand strong {
      font-family: var(--font-ui);
      font-size: 15px;
      letter-spacing: 0;
      white-space: nowrap;
    }
    .status-strip {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    main {
      padding: 0 0 40px;
      animation: enter 560ms cubic-bezier(.2, .8, .2, 1);
    }
    .hero {
      position: sticky;
      top: 57px;
      z-index: 20;
      max-width: 1180px;
      margin: 12px auto 28px;
      padding: 18px 24px;
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(280px, 0.9fr);
      gap: 24px;
      align-items: center;
      border: 1px solid rgba(255, 255, 255, 0.62);
      border-radius: 30px;
      background:
        linear-gradient(135deg, rgba(0, 102, 204, 0.08) 0%, rgba(255,255,255,0) 28%),
        linear-gradient(225deg, rgba(126, 63, 242, 0.06) 0%, rgba(255,255,255,0) 24%),
        rgba(251, 251, 253, 0.74);
      backdrop-filter: blur(24px) saturate(180%);
      -webkit-backdrop-filter: blur(24px) saturate(180%);
      box-shadow:
        0 18px 46px rgba(0, 0, 0, 0.07),
        inset 0 1px 0 rgba(255, 255, 255, 0.78);
    }
    .hero::after {
      content: "";
      position: absolute;
      left: 30px;
      right: 30px;
      bottom: -18px;
      height: 18px;
      pointer-events: none;
      background: linear-gradient(180deg, rgba(245, 245, 247, 0.38), rgba(245, 245, 247, 0));
      filter: blur(8px);
    }
    .eyebrow {
      font-family: "Avenir Next", var(--font-ui);
      color: var(--blue);
      font-size: 11px;
      font-weight: 700;
      margin-bottom: 7px;
    }
    h1 {
      font-family: var(--font-display);
      font-size: clamp(26px, 3vw, 42px);
      line-height: 1.02;
      letter-spacing: 0;
      margin: 0;
      max-width: 680px;
    }
    .lead {
      font-family: var(--font-cn);
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
      margin: 10px 0 0;
      max-width: 620px;
    }
    .meta {
      display: grid;
      grid-template-columns: repeat(2, minmax(160px, 1fr));
      gap: 10px;
    }
    .metric {
      min-height: 66px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
      backdrop-filter: blur(18px) saturate(160%);
      -webkit-backdrop-filter: blur(18px) saturate(160%);
      box-shadow: var(--shadow);
      padding: 12px;
      overflow: hidden;
    }
    .metric span {
      font-family: "Avenir Next", var(--font-ui);
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      margin-bottom: 6px;
    }
    .metric strong {
      font-family: var(--font-ui);
      display: block;
      font-size: 16px;
      line-height: 1.25;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .surface {
      max-width: 1180px;
      margin-left: auto;
      margin-right: auto;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--panel-strong);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .surface-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 18px 18px 14px;
      border-bottom: 1px solid var(--line);
    }
    .surface-title {
      font-family: var(--font-display);
      margin: 0;
      font-size: 20px;
      letter-spacing: 0;
    }
    .surface-subtitle {
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
    }
    button {
      border: 1px solid var(--line-strong);
      background: rgba(255,255,255,0.72);
      color: var(--text);
      height: 36px;
      padding: 0 14px;
      border-radius: 999px;
      cursor: pointer;
      font-weight: 650;
      transition: transform 180ms ease, background 180ms ease, border-color 180ms ease;
    }
    button:hover {
      transform: translateY(-1px);
      background: #ffffff;
      border-color: rgba(0, 102, 204, 0.38);
    }
    .table-wrap {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      background: transparent;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 14px 16px;
      text-align: left;
      vertical-align: top;
    }
    tr:last-child td { border-bottom: 0; }
    tbody tr {
      transition: background 160ms ease;
    }
    tbody tr:hover {
      background: rgba(0, 102, 204, 0.04);
    }
    th {
      font-family: "Avenir Next", var(--font-ui);
      font-size: 11px;
      color: var(--muted);
      font-weight: 750;
      text-transform: uppercase;
      background: rgba(245, 245, 247, 0.8);
    }
    .badge {
      font-family: "Avenir Next", var(--font-ui);
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 10px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 760;
      background: rgba(0, 102, 204, 0.1);
      color: var(--blue);
    }
    .running { background: rgba(0, 122, 90, 0.12); color: var(--ok); }
    .pending { background: rgba(180, 91, 0, 0.12); color: var(--warn); }
    .error { background: rgba(196, 30, 58, 0.1); color: var(--bad); }
    .muted { color: var(--muted); }
    .clip {
      max-width: 420px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .empty {
      padding: 42px 20px;
      text-align: center;
      color: var(--muted);
      background: linear-gradient(180deg, rgba(255,255,255,0.74), rgba(245,245,247,0.72));
    }
    .section {
      margin-top: 26px;
    }
    .prompt {
      font-family: var(--font-literary);
      max-width: 560px;
      white-space: normal;
      line-height: 1.55;
      color: #333336;
    }
    .cn-text {
      font-family: var(--font-cn);
    }
    .display-name {
      font-family: var(--font-display);
      font-size: 18px;
      font-weight: 760;
    }
    .english {
      font-family: "Avenir Next", var(--font-ui);
      letter-spacing: 0;
    }
    code {
      font-family: var(--font-mono);
      font-size: 12px;
      color: #313135;
      background: rgba(29,29,31,0.06);
      border: 1px solid rgba(29,29,31,0.08);
      border-radius: 8px;
      padding: 3px 7px;
    }
    @keyframes enter {
      from { opacity: 0; transform: translateY(14px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (prefers-reduced-motion: reduce) {
      * { animation: none !important; transition: none !important; }
    }
    @media (max-width: 760px) {
      .nav { padding: 12px 16px; align-items: flex-start; flex-direction: column; }
      main { padding: 0 0 32px; }
      .hero { top: 103px; grid-template-columns: 1fr; padding: 14px; margin: 10px 14px 18px; border-radius: 24px; }
      .meta { grid-template-columns: 1fr; }
      .surface { margin-left: 14px; margin-right: 14px; }
      .clip { max-width: 220px; }
      th:nth-child(2), td:nth-child(2), th:nth-child(6), td:nth-child(6), th:nth-child(7), td:nth-child(7) { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <div class="nav">
      <div class="brand">
        <div class="mark" aria-hidden="true"></div>
        <strong>WhatsApp Bridge Console</strong>
      </div>
      <div class="status-strip">
        <a class="badge" href="/setup">Setup</a>
        <span class="badge" id="connection">loading</span>
        <span class="badge" id="backend">loading</span>
        <span class="badge" id="updated">loading</span>
      </div>
    </div>
  </header>
  <main>
    <section class="hero">
      <div>
        <div class="eyebrow">LIVE OPERATIONS VIEW</div>
        <h1>清楚掌握每一次 WhatsApp AI 交互。</h1>
        <p class="lead">查看当前任务、最近 10 条交互、群角色、触发规则和严格限制。页面自动刷新，适合常驻在浏览器里观察运行状态。</p>
      </div>
      <div class="meta">
        <div class="metric"><span>Allowed Groups</span><strong id="group-count">loading</strong></div>
        <div class="metric"><span>Active Tasks</span><strong id="active-count">loading</strong></div>
      </div>
    </section>

    <section class="surface">
      <div class="surface-head">
        <div>
          <h2 class="surface-title">当前处理和最近 10 条交互</h2>
          <div class="surface-subtitle english">Running, pending, recent replies and errors.</div>
        </div>
        <button id="refresh" type="button">Refresh</button>
      </div>
      <div id="content" class="empty">Loading...</div>
    </section>

    <section class="surface section">
      <div class="surface-head">
        <div>
          <h2 class="surface-title">AI 群角色与任务定义</h2>
          <div class="surface-subtitle english">Names, triggers, identities and hard boundaries.</div>
        </div>
      </div>
      <div id="config" class="empty">Loading...</div>
    </section>
  </main>
  <script>
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
    const formatTime = (value) => value ? new Date(value).toLocaleString() : "-";
    const statusBadge = (task) => {
      if (task.lastError) return '<span class="badge error">error</span>';
      if (task.running) return '<span class="badge running">running</span>';
      if (task.pending) return '<span class="badge pending">pending</span>';
      return '<span class="badge">idle</span>';
    };
    const renderConfig = (groups) => {
      if (!groups.length) {
        document.getElementById("config").className = "empty";
        document.getElementById("config").textContent = "No group AI roles configured.";
        return;
      }
      document.getElementById("config").className = "table-wrap";
      document.getElementById("config").innerHTML = '<table><thead><tr><th>Group</th><th>AI Name</th><th>Trigger</th><th>Mode</th><th>Task</th><th>Role / Identity</th><th>Restrictions</th></tr></thead><tbody>' +
        groups.map((group) => '<tr>' +
          '<td><strong class="cn-text">' + escapeHtml(group.subject || "Unknown group") + '</strong><div class="muted clip english">' + escapeHtml(group.id) + '</div></td>' +
          '<td><strong class="display-name">' + escapeHtml(group.aiName || "-") + '</strong></td>' +
          '<td><code>' + escapeHtml(group.triggerPattern) + '</code></td>' +
          '<td><span class="badge">' + escapeHtml(group.mode) + '</span></td>' +
          '<td><span class="cn-text">' + escapeHtml(group.taskDefinition) + '</span></td>' +
          '<td><div class="prompt">' + escapeHtml(group.rolePrompt) + '</div></td>' +
          '<td><div class="prompt">' + escapeHtml((group.restrictions || []).join(" / ")) + '</div></td>' +
        '</tr>').join("") + '</tbody></table>';
    };
    async function refresh() {
      const [statusRes, tasksRes, configRes] = await Promise.all([fetch("/status"), fetch("/tasks"), fetch("/config")]);
      const status = await statusRes.json();
      const tasksData = await tasksRes.json();
      const configData = await configRes.json();
      const connectionEl = document.getElementById("connection");
      connectionEl.textContent = "Connection: " + (status.connectionState || "-");
      connectionEl.className = status.connectionState === "open" ? "badge running" : "badge pending";
      document.getElementById("backend").textContent = "Backend: " + (status.llmBackend || "-");
      document.getElementById("updated").textContent = "Updated: " + new Date().toLocaleTimeString();
      renderConfig(configData.groups || []);
      const tasks = tasksData.tasks || [];
      document.getElementById("group-count").textContent = String((configData.groups || []).length);
      document.getElementById("active-count").textContent = String(tasks.filter((task) => task.running || task.pending).length);
      if (!tasks.length) {
        document.getElementById("content").className = "empty";
        document.getElementById("content").textContent = "No tasks yet.";
        return;
      }
      document.getElementById("content").className = "table-wrap";
      document.getElementById("content").innerHTML = '<table><thead><tr><th>Status</th><th>Mode</th><th>Chat</th><th>Last message</th><th>Last reply / error</th><th>Received</th></tr></thead><tbody>' +
        tasks.map((task) => '<tr>' +
          '<td>' + statusBadge(task) + '</td>' +
          '<td>' + escapeHtml(task.mode) + '</td>' +
          '<td><div class="clip english">' + escapeHtml(task.chatId) + '</div><div class="muted english">' + escapeHtml(task.number || "") + '</div></td>' +
          '<td><div class="clip cn-text" title="' + escapeHtml(task.lastText) + '">' + escapeHtml(task.lastText) + '</div></td>' +
          '<td><div class="clip cn-text" title="' + escapeHtml(task.lastError || task.lastReply) + '">' + escapeHtml(task.lastError || task.lastReply || "-") + '</div></td>' +
          '<td>' + escapeHtml(formatTime(task.receivedAt)) + '</td>' +
        '</tr>').join("") + '</tbody></table>';
    }
    document.getElementById("refresh").addEventListener("click", refresh);
    refresh().catch((error) => {
      document.getElementById("content").className = "empty";
      document.getElementById("content").textContent = String(error.message || error);
    });
    setInterval(() => refresh().catch(() => {}), 3000);
  </script>
</body>
</html>`);
});

app.get("/setup", (_req, res) => {
  res.type("html").send(`<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WhatsApp Bridge Setup</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f5f7;
      --panel: rgba(255, 255, 255, 0.78);
      --panel-strong: rgba(255, 255, 255, 0.92);
      --text: #1d1d1f;
      --muted: #6e6e73;
      --line: rgba(29, 29, 31, 0.12);
      --line-strong: rgba(29, 29, 31, 0.18);
      --ok: #007a5a;
      --warn: #b45b00;
      --bad: #c41e3a;
      --blue: #0066cc;
      --shadow: 0 18px 55px rgba(0, 0, 0, 0.08);
      --font-ui: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", sans-serif;
      --font-display: "Songti SC", "STSong", "New York", "Times New Roman", serif;
      --font-cn: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(135deg, rgba(0, 102, 204, 0.10) 0%, rgba(255,255,255,0) 34%),
        linear-gradient(225deg, rgba(126, 63, 242, 0.08) 0%, rgba(255,255,255,0) 32%),
        linear-gradient(180deg, #fbfbfd 0%, var(--bg) 46%, #ffffff 100%);
      color: var(--text);
      font-family: var(--font-ui);
      font-size: 14px;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 30;
      border-bottom: 1px solid var(--line);
      background: rgba(251, 251, 253, 0.72);
      backdrop-filter: blur(22px) saturate(180%);
      -webkit-backdrop-filter: blur(22px) saturate(180%);
    }
    .nav {
      max-width: 1180px;
      margin: 0 auto;
      padding: 14px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .mark {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: conic-gradient(from 160deg, #1d1d1f, #0066cc, #20c997, #1d1d1f);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.38), 0 8px 18px rgba(0, 102, 204, 0.22);
    }
    .status-strip {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    a { color: inherit; text-decoration: none; }
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 42px 24px 44px;
      animation: enter 560ms cubic-bezier(.2, .8, .2, 1);
    }
    .hero {
      border: 1px solid rgba(255, 255, 255, 0.62);
      border-radius: 30px;
      background:
        linear-gradient(135deg, rgba(0, 102, 204, 0.08) 0%, rgba(255,255,255,0) 28%),
        linear-gradient(225deg, rgba(126, 63, 242, 0.06) 0%, rgba(255,255,255,0) 24%),
        rgba(251, 251, 253, 0.74);
      backdrop-filter: blur(24px) saturate(180%);
      -webkit-backdrop-filter: blur(24px) saturate(180%);
      box-shadow: var(--shadow), inset 0 1px 0 rgba(255, 255, 255, 0.78);
      padding: 26px;
      margin-bottom: 24px;
    }
    .eyebrow {
      font-family: "Avenir Next", var(--font-ui);
      color: var(--blue);
      font-size: 11px;
      font-weight: 760;
      margin-bottom: 8px;
    }
    h1 {
      font-family: var(--font-display);
      font-size: clamp(32px, 4vw, 54px);
      line-height: 1;
      margin: 0;
    }
    .lead {
      font-family: var(--font-cn);
      color: var(--muted);
      font-size: 15px;
      line-height: 1.55;
      margin: 14px 0 0;
      max-width: 780px;
    }
    .surface {
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--panel-strong);
      box-shadow: var(--shadow);
      overflow: hidden;
      margin-top: 18px;
    }
    .surface-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 18px;
      border-bottom: 1px solid var(--line);
    }
    .surface-title {
      font-family: var(--font-display);
      margin: 0;
      font-size: 20px;
    }
    .surface-subtitle {
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 10px;
      border-radius: 999px;
      font-family: "Avenir Next", var(--font-ui);
      font-size: 11px;
      font-weight: 760;
      background: rgba(0, 102, 204, 0.1);
      color: var(--blue);
    }
    .running { background: rgba(0, 122, 90, 0.12); color: var(--ok); }
    .pending { background: rgba(180, 91, 0, 0.12); color: var(--warn); }
    .setup-grid {
      display: grid;
      grid-template-columns: minmax(260px, 0.8fr) minmax(320px, 1.2fr);
      gap: 18px;
      padding: 18px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255,255,255,0.68);
      padding: 16px;
    }
    .card h3 {
      margin: 0 0 12px;
      font-size: 15px;
    }
    .row {
      display: grid;
      grid-template-columns: 160px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
      margin-top: 12px;
    }
    label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    input, select {
      width: 100%;
      height: 38px;
      border: 1px solid var(--line-strong);
      border-radius: 12px;
      background: rgba(255,255,255,0.8);
      color: var(--text);
      padding: 0 12px;
      font: inherit;
    }
    input:focus, select:focus {
      outline: 2px solid rgba(0, 102, 204, 0.18);
      border-color: rgba(0, 102, 204, 0.42);
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      margin-top: 8px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      padding: 0 18px 18px;
    }
    button {
      border: 1px solid var(--line-strong);
      background: rgba(255,255,255,0.76);
      color: var(--text);
      height: 38px;
      padding: 0 16px;
      border-radius: 999px;
      cursor: pointer;
      font-weight: 700;
      transition: transform 180ms ease, background 180ms ease, border-color 180ms ease;
    }
    button:hover {
      transform: translateY(-1px);
      background: #ffffff;
      border-color: rgba(0, 102, 204, 0.38);
    }
    .primary {
      background: #1d1d1f;
      color: #ffffff;
      border-color: #1d1d1f;
    }
    .message {
      min-height: 22px;
      color: var(--muted);
      padding: 0 18px 18px;
    }
    code {
      font-family: var(--font-mono);
      background: rgba(29,29,31,0.06);
      border: 1px solid rgba(29,29,31,0.08);
      border-radius: 8px;
      padding: 3px 7px;
    }
    @keyframes enter {
      from { opacity: 0; transform: translateY(14px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (max-width: 820px) {
      .nav { padding: 12px 16px; align-items: flex-start; flex-direction: column; }
      main { padding: 24px 14px 32px; }
      .setup-grid { grid-template-columns: 1fr; padding: 14px; }
      .row { grid-template-columns: 1fr; gap: 6px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="nav">
      <a class="brand" href="/">
        <div class="mark" aria-hidden="true"></div>
        <strong>WhatsApp Bridge Console</strong>
      </a>
      <div class="status-strip">
        <a class="badge" href="/">Dashboard</a>
        <span class="badge" id="current-mode">loading</span>
      </div>
    </div>
  </header>
  <main>
    <section class="hero">
      <div class="eyebrow">MODEL SETUP</div>
      <h1>大模型对接设置。</h1>
      <p class="lead">这里管理 WhatsApp Bridge 当前使用的大模型模式。保存会写入 <code>.env</code>；由于服务启动时读取配置，保存后需要重启生效。</p>
    </section>
    <section class="surface">
      <div class="surface-head">
        <div>
          <h2 class="surface-title">当前对接模式</h2>
          <div class="surface-subtitle">Active runtime and saved environment values.</div>
        </div>
        <span class="badge pending" id="restart-note">restart required after save</span>
      </div>
      <div class="setup-grid">
        <div class="card">
          <h3>Runtime</h3>
          <div class="row"><label>Active backend</label><strong id="runtime-backend">-</strong></div>
          <div class="row"><label>OpenAI model</label><span id="runtime-openai-model">-</span></div>
          <div class="row"><label>Base URL</label><span id="runtime-base-url">-</span></div>
          <div class="row"><label>Codex model</label><span id="runtime-codex-model">-</span></div>
          <div class="row"><label>Codex workdir</label><span id="runtime-codex-workdir">-</span></div>
        </div>
        <div class="card">
          <h3>Saved Configuration</h3>
          <div class="row">
            <label for="llmBackend">Provider mode</label>
            <select id="llmBackend">
              <option value="codex">Codex CLI</option>
              <option value="openai">OpenAI compatible API</option>
            </select>
          </div>
          <div class="row">
            <label for="openaiModel">API model</label>
            <select id="openaiModel">
              <option value="gpt-5.2">OpenAI GPT-5.2</option>
              <option value="gpt-5.1">OpenAI GPT-5.1</option>
              <option value="gpt-4.1">OpenAI GPT-4.1</option>
              <option value="gpt-4.1-mini">OpenAI GPT-4.1 Mini</option>
              <option value="deepseek-chat">DeepSeek Chat</option>
              <option value="deepseek-reasoner">DeepSeek Reasoner</option>
              <option value="qwen-plus">Qwen Plus</option>
              <option value="qwen-max">Qwen Max</option>
              <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
              <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
              <option value="custom">Custom...</option>
            </select>
          </div>
          <div class="row" id="customModelRow" style="display: none;">
            <label for="customOpenaiModel">Custom model</label>
            <input id="customOpenaiModel" placeholder="provider-specific model id" />
          </div>
          <div class="row">
            <label for="openaiBaseUrl">API base URL</label>
            <input id="openaiBaseUrl" placeholder="empty for OpenAI default, or compatible endpoint" />
          </div>
          <div class="row">
            <label for="openaiApiKey">API key</label>
            <input id="openaiApiKey" type="password" placeholder="leave blank to keep existing key" />
          </div>
          <div class="hint">API key 不会回显完整内容；留空表示保留原值。</div>
          <div class="row">
            <label for="codexCliPath">Codex CLI path</label>
            <input id="codexCliPath" placeholder="codex" />
          </div>
          <div class="row">
            <label for="codexModel">Codex model</label>
            <input id="codexModel" placeholder="empty uses CLI default" />
          </div>
          <div class="row">
            <label for="codexWorkdir">Codex workdir</label>
            <input id="codexWorkdir" placeholder="/Users/zhangwei/python" />
          </div>
          <div class="row">
            <label for="codexReasoningEffort">Reasoning effort</label>
            <select id="codexReasoningEffort">
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="xhigh">xhigh</option>
            </select>
          </div>
          <div class="row">
            <label for="codexTimeoutMs">Codex timeout ms</label>
            <input id="codexTimeoutMs" inputmode="numeric" placeholder="120000" />
          </div>
        </div>
      </div>
      <div class="actions">
        <button class="primary" id="save" type="button">Save configuration</button>
        <button id="restart" type="button">Restart bridge</button>
        <button id="reload" type="button">Reload values</button>
      </div>
      <div class="message" id="message"></div>
    </section>
  </main>
  <script>
    const fields = ["llmBackend", "openaiBaseUrl", "openaiApiKey", "codexCliPath", "codexModel", "codexWorkdir", "codexReasoningEffort", "codexTimeoutMs"];
    const setText = (id, value) => { document.getElementById(id).textContent = value || "-"; };
    const setMessage = (text) => { document.getElementById("message").textContent = text; };
    const modelOptions = Array.from(document.getElementById("openaiModel").options).map((option) => option.value);
    const setModelValue = (model) => {
      const value = model || "gpt-5.2";
      if (modelOptions.includes(value) && value !== "custom") {
        document.getElementById("openaiModel").value = value;
        document.getElementById("customModelRow").style.display = "none";
        document.getElementById("customOpenaiModel").value = "";
      } else {
        document.getElementById("openaiModel").value = "custom";
        document.getElementById("customModelRow").style.display = "";
        document.getElementById("customOpenaiModel").value = value;
      }
    };
    const getModelValue = () => {
      const selected = document.getElementById("openaiModel").value;
      if (selected === "custom") {
        return document.getElementById("customOpenaiModel").value.trim();
      }
      return selected;
    };
    async function loadSetup() {
      const data = await fetch("/setup-data").then((res) => res.json());
      document.getElementById("current-mode").textContent = "Mode: " + data.current.llmBackend;
      setText("runtime-backend", data.current.llmBackend);
      setText("runtime-openai-model", data.current.openaiModel);
      setText("runtime-base-url", data.current.openaiBaseUrl || "OpenAI default");
      setText("runtime-codex-model", data.current.codexModel || "CLI default");
      setText("runtime-codex-workdir", data.current.codexWorkdir);
      document.getElementById("llmBackend").value = data.env.llmBackend || data.current.llmBackend || "codex";
      setModelValue(data.env.openaiModel || data.current.openaiModel || "gpt-5.2");
      document.getElementById("openaiBaseUrl").value = data.env.openaiBaseUrl || "";
      document.getElementById("openaiApiKey").placeholder = data.env.openaiApiKeyMasked ? "existing: " + data.env.openaiApiKeyMasked : "leave blank to keep existing key";
      document.getElementById("codexCliPath").value = data.env.codexCliPath || data.current.codexCliPath || "codex";
      document.getElementById("codexModel").value = data.env.codexModel || "";
      document.getElementById("codexWorkdir").value = data.env.codexWorkdir || data.current.codexWorkdir || "";
      document.getElementById("codexReasoningEffort").value = data.env.codexReasoningEffort || data.current.codexReasoningEffort || "medium";
      document.getElementById("codexTimeoutMs").value = data.env.codexTimeoutMs || String(data.current.codexTimeoutMs || 120000);
    }
    async function saveSetup() {
      const payload = Object.fromEntries(fields.map((id) => [id, document.getElementById(id).value.trim()]));
      payload.openaiModel = getModelValue();
      if (!payload.openaiModel) {
        throw new Error("API model is required.");
      }
      const response = await fetch("/setup-data", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "save failed");
      setMessage("Saved to .env. Restart bridge to apply.");
      document.getElementById("openaiApiKey").value = "";
      await loadSetup();
    }
    document.getElementById("openaiModel").addEventListener("change", () => {
      document.getElementById("customModelRow").style.display =
        document.getElementById("openaiModel").value === "custom" ? "" : "none";
    });
    async function restartBridge() {
      setMessage("Restarting bridge...");
      await fetch("/setup-restart", { method: "POST" });
      setTimeout(() => {
        window.location.reload();
      }, 5000);
    }
    document.getElementById("save").addEventListener("click", () => saveSetup().catch((error) => setMessage(error.message)));
    document.getElementById("restart").addEventListener("click", () => restartBridge().catch((error) => setMessage(error.message)));
    document.getElementById("reload").addEventListener("click", () => loadSetup().catch((error) => setMessage(error.message)));
    loadSetup().catch((error) => setMessage(error.message));
  </script>
</body>
</html>`);
});

app.get("/setup-data", (_req, res) => {
  res.json(getSetupSnapshot());
});

app.post("/setup-data", (req, res) => {
  const body = req.body || {};
  const llmBackend = String(body.llmBackend || "").trim().toLowerCase();
  if (!["codex", "openai"].includes(llmBackend)) {
    res.status(400).json({ status: "error", error: "invalid_llm_backend" });
    return;
  }

  const currentEnv = parseEnvFile();
  const updates = {
    LLM_BACKEND: llmBackend,
    OPENAI_MODEL: String(body.openaiModel || currentEnv.OPENAI_MODEL || "gpt-5.2").trim(),
    OPENAI_BASE_URL: String(body.openaiBaseUrl || "").trim(),
    CODEX_CLI_PATH: String(body.codexCliPath || currentEnv.CODEX_CLI_PATH || "codex").trim(),
    CODEX_MODEL: String(body.codexModel || "").trim(),
    CODEX_WORKDIR: String(body.codexWorkdir || currentEnv.CODEX_WORKDIR || CODEX_WORKDIR).trim(),
    CODEX_REASONING_EFFORT: String(
      body.codexReasoningEffort || currentEnv.CODEX_REASONING_EFFORT || "medium"
    ).trim(),
    CODEX_TIMEOUT_MS: String(body.codexTimeoutMs || currentEnv.CODEX_TIMEOUT_MS || "120000").trim(),
  };

  const apiKey = String(body.openaiApiKey || "").trim();
  if (apiKey && !/^\*+$/.test(apiKey) && !apiKey.includes("...")) {
    updates.OPENAI_API_KEY = apiKey;
  }

  if (!/^\d+$/.test(updates.CODEX_TIMEOUT_MS)) {
    res.status(400).json({ status: "error", error: "invalid_codex_timeout_ms" });
    return;
  }
  if (!["low", "medium", "high", "xhigh"].includes(updates.CODEX_REASONING_EFFORT)) {
    res.status(400).json({ status: "error", error: "invalid_codex_reasoning_effort" });
    return;
  }

  setEnvValues(updates);
  res.json({
    status: "ok",
    saved: true,
    restartRequired: true,
    setup: getSetupSnapshot(),
  });
});

app.post("/setup-restart", (_req, res) => {
  res.json({ status: "ok", restarting: true });
  setTimeout(() => {
    process.exit(0);
  }, 250);
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

app.get("/status", (_req, res) => {
  res.json({
    status: "ok",
    connectionState,
    linkedJid,
    hasPendingQr: Boolean(lastQr),
    llmBackend: LLM_BACKEND,
    allowedGroupJids: Array.from(ALLOWED_GROUP_JIDS),
    groupTriggerPattern: GROUP_TRIGGER_PATTERN,
    authDir: AUTH_DIR,
    stateDir: STATE_DIR,
  });
});

app.get("/tasks", (_req, res) => {
  res.json({
    status: "ok",
    tasks: getTaskSnapshot(),
  });
});

app.get("/config", async (_req, res) => {
  res.json({
    status: "ok",
    selfChat: {
      mode: "assistant",
      rolePrompt: SYSTEM_PROMPT,
    },
    groups: await getAllowedGroupConfigSnapshot(),
  });
});

app.get("/groups", async (_req, res) => {
  try {
    if (!sock || connectionState !== "open") {
      res.status(503).json({ status: "error", error: "whatsapp_not_connected" });
      return;
    }
    const groups = await sock.groupFetchAllParticipating();
    res.json({
      status: "ok",
      groups: Object.values(groups).map((group) => ({
        id: group.id,
        subject: group.subject,
        participants: group.participants?.length ?? null,
      })),
    });
  } catch (error) {
    res.status(500).json({
      status: "error",
      error: String(error?.message || error || "groups_fetch_failed"),
    });
  }
});

app.get("/qr", (_req, res) => {
  res.json({
    status: "ok",
    hasPendingQr: Boolean(lastQr),
    qrTextFile: fs.existsSync(QR_TXT_FILE) ? QR_TXT_FILE : null,
    qrSvgFile: fs.existsSync(QR_SVG_FILE) ? QR_SVG_FILE : null,
  });
});

app.post("/logout", async (_req, res) => {
  isShuttingDown = true;
  try {
    if (sock) {
      await sock.logout();
    }
  } catch (error) {
    console.error("logout failed:", error);
  }
  fs.rmSync(AUTH_DIR, { recursive: true, force: true });
  res.json({ status: "ok", loggedOut: true });
  process.exit(0);
});

app.post("/send-test", async (req, res) => {
  try {
    const to = req.body?.to || linkedJid;
    const text = req.body?.text;
    if (!sock || connectionState !== "open") {
      res.status(503).json({ status: "error", error: "whatsapp_not_connected" });
      return;
    }
    if (!to) {
      res.status(400).json({ status: "error", error: "missing_target" });
      return;
    }
    if (!text || !String(text).trim()) {
      res.status(400).json({ status: "error", error: "missing_text" });
      return;
    }
    await sendText(to, String(text).trim());
    res.json({ status: "ok", to, sent: true });
  } catch (error) {
    res.status(500).json({
      status: "error",
      error: String(error?.message || error || "send_failed"),
    });
  }
});

app.listen(PORT, () => {
  console.log(`status server listening on http://127.0.0.1:${PORT}`);
});

await startSocket();

process.on("SIGINT", async () => {
  isShuttingDown = true;
  process.exit(0);
});

process.on("SIGTERM", async () => {
  isShuttingDown = true;
  process.exit(0);
});
