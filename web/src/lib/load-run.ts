import type { Message, RunSummary } from './types';

const summaryLoaders = import.meta.glob<{ default: RunSummary }>('../data/runs/*/summary.json');
const messageLoaders = import.meta.glob<{ default: { messages: Message[] } }>(
  '../data/runs/*/messages.json',
);

export function summaryKey(id: string): string {
  return `../data/runs/${id}/summary.json`;
}

export function messagesKey(id: string): string {
  return `../data/runs/${id}/messages.json`;
}

export async function loadSummary(id: string): Promise<RunSummary | null> {
  const loader = summaryLoaders[summaryKey(id)];
  if (!loader) return null;
  const module = await loader();
  return module.default;
}

export async function loadMessages(id: string): Promise<Message[] | null> {
  const loader = messageLoaders[messagesKey(id)];
  if (!loader) return null;
  const module = await loader();
  return module.default.messages;
}
