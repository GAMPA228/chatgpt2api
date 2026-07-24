"use client";

import localforage from "localforage";

import { fetchImageTasks, type ImageModel, type ImageTask } from "@/lib/api";

export type ImageConversationMode = "generate" | "edit";

export type StoredReferenceImage = {
  name: string;
  type: string;
  dataUrl: string;
};

export type StoredImage = {
  id: string;
  taskId?: string;
  status?: "loading" | "success" | "error";
  taskStatus?: "queued" | "running";
  progress?: string;
  b64_json?: string;
  url?: string;
  revised_prompt?: string;
  error?: string;
  startTime?: number;
  elapsedSecs?: number;
  elapsedUpdatedAt?: number;
  durationMs?: number;
};

export type ImageTurnStatus = "queued" | "generating" | "success" | "error";

export type ImageTurn = {
  id: string;
  prompt: string;
  model: ImageModel;
  mode: ImageConversationMode;
  referenceImages: StoredReferenceImage[];
  count: number;
  size: string;
  ratio: string;
  tier: string;
  quality: string;
  images: StoredImage[];
  createdAt: string;
  status: ImageTurnStatus;
  error?: string;
  promptDeleted?: boolean;
  resultsDeleted?: boolean;
};

export type ImageConversation = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  turns: ImageTurn[];
};

export type ImageConversationStats = {
  queued: number;
  running: number;
};

const imageConversationStorage = localforage.createInstance({
  name: "chatgpt2api",
  storeName: "image_conversations",
});

const IMAGE_CONVERSATIONS_KEY = "items";
const IMAGE_TASK_RECOVERY_PAGE_LIMIT = 100;
// Server history can contain years of base64 results. Recovery is deliberately
// bounded: loading a missing browser history must never rebuild the entire
// archive in one tab or one request burst.
const IMAGE_TASK_RECOVERY_MAX_RESULTS = 25;
let imageConversationWriteQueue: Promise<void> = Promise.resolve();

function normalizeStoredImage(image: StoredImage): StoredImage {
  const normalized = {
    ...image,
    taskId: typeof image.taskId === "string" && image.taskId ? image.taskId : undefined,
    taskStatus: image.taskStatus === "queued" || image.taskStatus === "running" ? image.taskStatus : undefined,
    url: typeof image.url === "string" && image.url ? image.url : undefined,
    revised_prompt: typeof image.revised_prompt === "string" ? image.revised_prompt : undefined,
    startTime: typeof image.startTime === "number" ? image.startTime : undefined,
    elapsedSecs: typeof image.elapsedSecs === "number" ? image.elapsedSecs : undefined,
    elapsedUpdatedAt: typeof image.elapsedUpdatedAt === "number" ? image.elapsedUpdatedAt : undefined,
    durationMs: typeof image.durationMs === "number" ? image.durationMs : undefined,
  };
  if (image.status === "loading" || image.status === "error" || image.status === "success") {
    // A server URL is durable across browser crashes. Do not retain base64
    // alongside it in IndexedDB: recovery would duplicate multi-MB strings.
    return normalized.url ? { ...normalized, b64_json: undefined } : normalized;
  }
  return {
    ...normalized,
    status: image.b64_json || image.url ? "success" : "loading",
  };
}

function normalizeReferenceImage(image: StoredReferenceImage): StoredReferenceImage {
  return {
    name: image.name || "reference.png",
    type: image.type || "image/png",
    dataUrl: image.dataUrl,
  };
}

function dataUrlMimeType(dataUrl: string) {
  const match = dataUrl.match(/^data:(.*?);base64,/);
  return match?.[1] || "image/png";
}

function getLegacyReferenceImages(source: Record<string, unknown>): StoredReferenceImage[] {
  if (Array.isArray(source.referenceImages)) {
    return source.referenceImages
      .filter((image): image is StoredReferenceImage => {
        if (!image || typeof image !== "object") {
          return false;
        }
        const candidate = image as StoredReferenceImage;
        return typeof candidate.dataUrl === "string" && candidate.dataUrl.length > 0;
      })
      .map(normalizeReferenceImage);
  }

  if (source.sourceImage && typeof source.sourceImage === "object") {
    const image = source.sourceImage as { dataUrl?: unknown; fileName?: unknown };
    if (typeof image.dataUrl === "string" && image.dataUrl) {
      return [
        {
          name: typeof image.fileName === "string" && image.fileName ? image.fileName : "reference.png",
          type: dataUrlMimeType(image.dataUrl),
          dataUrl: image.dataUrl,
        },
      ];
    }
  }

  return [];
}

function normalizeTurn(turn: ImageTurn & Record<string, unknown>): ImageTurn {
  const normalizedImages = Array.isArray(turn.images) ? turn.images.map(normalizeStoredImage) : [];
  const derivedStatus: ImageTurnStatus =
    normalizedImages.some((image) => image.status === "loading")
      ? "generating"
      : normalizedImages.some((image) => image.status === "error")
        ? "error"
        : "success";

  return {
    id: String(turn.id || `${Date.now()}`),
    prompt: String(turn.prompt || ""),
    model: (turn.model as ImageModel) || "gpt-image-2",
    mode: turn.mode === "edit" ? "edit" : "generate",
    referenceImages: getLegacyReferenceImages(turn),
    count: Math.max(1, Number(turn.count || normalizedImages.length || 1)),
    size: typeof turn.size === "string" ? turn.size : "",
    ratio: typeof turn.ratio === "string" && turn.ratio ? turn.ratio : "1:1",
    tier: typeof turn.tier === "string" && turn.tier ? turn.tier : "1k",
    quality: typeof turn.quality === "string" && turn.quality ? turn.quality : "auto",
    images: normalizedImages,
    createdAt: String(turn.createdAt || new Date().toISOString()),
    status:
      turn.status === "queued" ||
      turn.status === "generating" ||
      turn.status === "success" ||
      turn.status === "error"
        ? turn.status
        : derivedStatus,
    error: typeof turn.error === "string" ? turn.error : undefined,
    promptDeleted: turn.promptDeleted === true,
    resultsDeleted: turn.resultsDeleted === true,
  };
}

function normalizeConversation(conversation: ImageConversation & Record<string, unknown>): ImageConversation {
  const turns = Array.isArray(conversation.turns)
    ? conversation.turns.map((turn) => normalizeTurn(turn as ImageTurn & Record<string, unknown>))
    : [
        normalizeTurn({
          id: String(conversation.id || `${Date.now()}`),
          prompt: String(conversation.prompt || ""),
          model: (conversation.model as ImageModel) || "gpt-image-2",
          mode: conversation.mode === "edit" ? "edit" : "generate",
          referenceImages: getLegacyReferenceImages(conversation),
          count: Number(conversation.count || 1),
          size: typeof conversation.size === "string" ? conversation.size : "",
          ratio: typeof conversation.ratio === "string" && conversation.ratio ? conversation.ratio : "1:1",
          tier: typeof conversation.tier === "string" && conversation.tier ? conversation.tier : "1k",
          quality: typeof conversation.quality === "string" && conversation.quality ? conversation.quality : "auto",
          images: Array.isArray(conversation.images) ? (conversation.images as StoredImage[]) : [],
          createdAt: String(conversation.createdAt || new Date().toISOString()),
          status:
            conversation.status === "generating" || conversation.status === "success" || conversation.status === "error"
              ? conversation.status
              : "success",
          error: typeof conversation.error === "string" ? conversation.error : undefined,
        }),
      ];
  const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;

  return {
    id: String(conversation.id || `${Date.now()}`),
    title: String(conversation.title || ""),
    createdAt: String(conversation.createdAt || lastTurn?.createdAt || new Date().toISOString()),
    updatedAt: String(conversation.updatedAt || lastTurn?.createdAt || new Date().toISOString()),
    turns,
  };
}

function sortImageConversations(conversations: ImageConversation[]): ImageConversation[] {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function getTimestamp(value: string) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function pickLatestConversation(current: ImageConversation, next: ImageConversation) {
  return getTimestamp(next.updatedAt) >= getTimestamp(current.updatedAt) ? next : current;
}

function queueImageConversationWrite<T>(operation: () => Promise<T>): Promise<T> {
  const result = imageConversationWriteQueue.then(operation);
  imageConversationWriteQueue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

async function readStoredImageConversations(): Promise<ImageConversation[]> {
  const items =
    (await imageConversationStorage.getItem<Array<ImageConversation & Record<string, unknown>>>(
      IMAGE_CONVERSATIONS_KEY,
    )) || [];
  return items.map(normalizeConversation);
}

function taskDataToStoredImage(image: StoredImage, task: ImageTask): StoredImage {
  if (task.status === "success") {
    const first = task.data?.[0];
    if (!first?.b64_json && !first?.url) {
      // Metadata-only history reconciliation must preserve the result already
      // stored in IndexedDB. A full task fetch is reserved for active tasks.
      if (image.status === "success" && (image.b64_json || image.url)) {
        return { ...image, taskId: task.id, durationMs: task.duration_ms };
      }
      return { ...image, status: "error", taskStatus: undefined, progress: undefined, error: "未返回图片数据" };
    }
    return {
      ...image,
      taskId: task.id,
      status: "success",
      taskStatus: undefined,
      progress: undefined,
      // The task service always saves a URL. Keeping b64_json here made each
      // polling response, React state update and IndexedDB write retain a
      // multi-megabyte duplicate until Chromium exhausted renderer memory.
      b64_json: first.url ? undefined : first.b64_json,
      url: first.url,
      revised_prompt: first.revised_prompt,
      error: undefined,
      durationMs: task.duration_ms,
    };
  }
  if (task.status === "error") {
    return { ...image, taskId: task.id, status: "error", taskStatus: undefined, progress: undefined, error: task.error || "生成失败", durationMs: task.duration_ms };
  }
  return { ...image, taskId: task.id, status: "loading", taskStatus: task.status, progress: task.progress, error: undefined };
}

function taskToRecoveredConversation(task: ImageTask): ImageConversation {
  const createdAt = task.created_at || new Date().toISOString();
  const prompt = task.prompt || "已恢复的生图记录";
  const image = taskDataToStoredImage({ id: task.id, taskId: task.id, status: "loading" }, task);
  const status: ImageTurnStatus = task.status === "success" ? "success" : task.status === "error" ? "error" : "generating";
  return {
    id: `recovered-${task.id}`,
    title: buildRecoveredConversationTitle(prompt),
    createdAt,
    updatedAt: task.updated_at || createdAt,
    turns: [{
      id: task.id,
      prompt,
      model: task.model || "gpt-image-2",
      mode: task.mode,
      referenceImages: [],
      count: 1,
      size: task.size || "",
      ratio: "1:1",
      tier: "1k",
      quality: task.quality || "auto",
      images: [image],
      createdAt,
      status,
      error: task.status === "error" ? task.error : undefined,
    }],
  };
}

function buildRecoveredConversationTitle(prompt: string) {
  const trimmed = prompt.trim();
  return trimmed.length <= 12 ? trimmed : `${trimmed.slice(0, 12)}...`;
}

function taskHasImageData(task: ImageTask): boolean {
  return task.data?.some((item) => Boolean(item.b64_json || item.url)) === true;
}

async function reconcileImageTasks(conversations: ImageConversation[]): Promise<ImageConversation[]> {
  const taskIds = Array.from(new Set(conversations.flatMap((conversation) =>
    conversation.turns.flatMap((turn) => turn.resultsDeleted ? [] : turn.images.flatMap((image) => image.taskId ? [image.taskId] : [])),
  )));
  const tasksRequiringResultData = Array.from(new Set(conversations.flatMap((conversation) =>
    conversation.turns.flatMap((turn) => turn.resultsDeleted ? [] : turn.images.flatMap((image) =>
      image.status !== "success" && image.taskId ? [image.taskId] : [],
    )),
  )));
  if (taskIds.length === 0) return conversations;

  // History reconciliation needs task status/timestamps, not image payloads.
  // Fetch image data only for still-running tasks which need a final result.
  const taskMap = new Map<string, ImageTask>();
  for (let offset = 0; offset < taskIds.length; offset += 100) {
    const { items } = await fetchImageTasks(taskIds.slice(offset, offset + 100), { includeData: false });
    for (const task of items) taskMap.set(task.id, task);
  }
  // The API intentionally never returns data for multiple IDs. Resolve each
  // completed task separately so a history restore cannot retain a batch of
  // base64 images in memory at once.
  for (const taskId of tasksRequiringResultData) {
    const { items } = await fetchImageTasks([taskId]);
    const task = items[0];
    if (task) taskMap.set(task.id, task);
  }

  const next = [...conversations];
  for (let index = 0; index < next.length; index += 1) {
      const conversation = next[index];
      let changed = false;
      const turns = conversation.turns.map((turn) => {
        if (turn.resultsDeleted) return turn;
        let turnChanged = false;
        const images = turn.images.map((image) => {
          const task = image.taskId ? taskMap.get(image.taskId) : undefined;
          if (!task) return image;
          const updated = taskDataToStoredImage(image, task);
          if (JSON.stringify(updated) !== JSON.stringify(image)) turnChanged = true;
          return updated;
        });
        if (!turnChanged) return turn;
        changed = true;
        const loading = images.some((image) => image.status === "loading");
        const failed = images.some((image) => image.status === "error");
        const status: ImageTurnStatus = loading ? "generating" : failed ? "error" : "success";
        return { ...turn, images, status };
      });
      if (changed) {
        // Reconciliation runs when history is opened. It must not stamp every
        // historical conversation with the browser's current time; use the
        // task's own last-update timestamp, while preserving a newer local
        // conversation timestamp (for example after a rename).
        const taskUpdatedAt = turns
          .flatMap((turn) => turn.images)
          .map((image) => image.taskId ? taskMap.get(image.taskId)?.updated_at : undefined)
          .filter((value): value is string => typeof value === "string" && Number.isFinite(new Date(value).getTime()))
          .reduce((latest, value) => getTimestamp(value) > getTimestamp(latest) ? value : latest, conversation.updatedAt);
        next[index] = { ...conversation, turns, updatedAt: taskUpdatedAt };
      }
  }
  return next;
}

async function recoverMissingConversations(conversations: ImageConversation[]): Promise<ImageConversation[]> {
  const knownTaskIds = new Set(conversations.flatMap((conversation) => conversation.turns.flatMap((turn) => turn.images.map((image) => image.taskId).filter(Boolean))));
  const recovered: ImageConversation[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | undefined;

  do {
    const page = await fetchImageTasks([], {
      includeData: false,
      limit: IMAGE_TASK_RECOVERY_PAGE_LIMIT,
      cursor,
    });
    const pageTasks = page.items
      .filter((task) => !knownTaskIds.has(task.id))
      .slice(0, IMAGE_TASK_RECOVERY_MAX_RESULTS - recovered.length);
    const successTaskIds = pageTasks
      .filter((task) => task.status === "success")
      .map((task) => task.id);
    const dataTaskMap = new Map<string, ImageTask>();

    // Fetch one completed image at a time. This intentionally trades a little
    // latency for a hard client/server memory bound on base64 payloads.
    for (const taskId of successTaskIds) {
      const { items } = await fetchImageTasks([taskId]);
      const task = items[0];
      if (task) dataTaskMap.set(task.id, task);
    }

    for (const task of pageTasks) {
      if (task.status === "success") {
        const taskWithData = dataTaskMap.get(task.id);
        if (!taskWithData || !taskHasImageData(taskWithData)) {
          continue;
        }
        recovered.push(taskToRecoveredConversation(taskWithData));
      } else {
        recovered.push(taskToRecoveredConversation(task));
      }
      knownTaskIds.add(task.id);
    }

    const nextCursor = page.next_cursor || page.next_before;
    if (recovered.length >= IMAGE_TASK_RECOVERY_MAX_RESULTS || !nextCursor || seenCursors.has(nextCursor)) {
      cursor = undefined;
    } else {
      seenCursors.add(nextCursor);
      cursor = nextCursor;
    }
  } while (cursor);

  return recovered.length > 0 ? [...conversations, ...recovered] : conversations;
}

export async function listImageConversations(options: { recover?: boolean } = {}): Promise<ImageConversation[]> {
  const local = await readStoredImageConversations();
  try {
    const reconciled = await reconcileImageTasks(local);
    const resolved = options.recover ? await recoverMissingConversations(reconciled) : reconciled;
    if (resolved.length !== local.length || resolved.some((item, index) => item !== local[index])) {
      await imageConversationStorage.setItem(IMAGE_CONVERSATIONS_KEY, sortImageConversations(resolved));
    }
    return sortImageConversations(resolved);
  } catch {
    return sortImageConversations(local);
  }
}

export async function recoverImageConversations(): Promise<ImageConversation[]> {
  return listImageConversations({ recover: true });
}

export async function saveImageConversations(conversations: ImageConversation[]): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const conversationMap = new Map(items.map((item) => [item.id, item]));
    for (const conversation of conversations.map(normalizeConversation)) {
      const current = conversationMap.get(conversation.id);
      conversationMap.set(conversation.id, current ? pickLatestConversation(current, conversation) : conversation);
    }
    await imageConversationStorage.setItem(
      IMAGE_CONVERSATIONS_KEY,
      sortImageConversations([...conversationMap.values()]),
    );
  });
}

export async function saveImageConversation(conversation: ImageConversation): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const nextConversation = normalizeConversation(conversation);
    const current = items.find((item) => item.id === nextConversation.id);
    const persistedConversation = current ? pickLatestConversation(current, nextConversation) : nextConversation;
    const nextItems = sortImageConversations([
      persistedConversation,
      ...items.filter((item) => item.id !== persistedConversation.id),
    ]);
    await imageConversationStorage.setItem(IMAGE_CONVERSATIONS_KEY, nextItems);
  });
}

export async function renameImageConversation(id: string, title: string): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const target = items.find((item) => item.id === id);
    if (!target) return;
    const updated = { ...target, title, updatedAt: new Date().toISOString() };
    const nextItems = sortImageConversations([
      updated,
      ...items.filter((item) => item.id !== id),
    ]);
    await imageConversationStorage.setItem(IMAGE_CONVERSATIONS_KEY, nextItems);
  });
}

export async function deleteImageConversation(id: string): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    await imageConversationStorage.setItem(
      IMAGE_CONVERSATIONS_KEY,
      items.filter((item) => item.id !== id),
    );
  });
}

export async function clearImageConversations(): Promise<void> {
  await queueImageConversationWrite(async () => {
    await imageConversationStorage.removeItem(IMAGE_CONVERSATIONS_KEY);
  });
}

export function getImageConversationStats(conversation: ImageConversation | null): ImageConversationStats {
  if (!conversation) {
    return { queued: 0, running: 0 };
  }

  return conversation.turns.reduce(
    (acc, turn) => {
      if (turn.resultsDeleted) {
        return acc;
      }
      if (turn.status === "queued") {
        acc.queued += 1;
      } else if (turn.status === "generating") {
        acc.running += 1;
      }
      return acc;
    },
    { queued: 0, running: 0 },
  );
}
