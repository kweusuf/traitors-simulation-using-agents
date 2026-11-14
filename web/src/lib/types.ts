export type Channel = 'public' | 'private' | 'traitor' | 'host';

export interface PlayerStats {
  public: number;
  private: number;
  traitor: number;
  host: number;
  accusationsMade: number;
  accusationsReceived: number;
  votesCast: number;
  votesReceived: number;
  conversations: { id: string; count: number }[];
}

export interface Player {
  id: string;
  name: string;
  role: 'faithful' | 'traitor';
  ambition?: 'solo' | 'team';
  outcome: string;
  exit?: { round: number; method: string; tally?: Record<string, number> };
  items: string[];
  roleByRound: Record<string, 'faithful' | 'traitor'>;
  aliveByRound: Record<string, boolean>;
  stats: PlayerStats;
  persona?: {
    description?: string;
    traits: Record<string, number>;
    goals?: Record<string, unknown>;
  };
}

export interface Vote {
  from: string;
  to: string;
  weight: number;
}

export interface RoundDigest {
  r: number;
  missions: number;
  items: { item: string; holder: string | null }[];
  hostWarnings: number;
  votes: Vote[];
  endVotes: { player: string | null; choice: string | null }[];
  nominations?: {
    nominees: string[];
    counts: Record<string, number>;
    accusations: Record<string, string>;
  };
  revote?: { called?: boolean; resolved?: Record<string, number> };
  council?: {
    proposals: { by: string | null; target: string | null; reason?: string }[];
    verdict?: {
      final?: Record<string, string>;
      dissent?: string[];
      unanimous?: boolean;
      tiebreak?: string;
    };
  };
  recruit?: {
    choice?: string;
    target?: string;
    by?: string;
    accepted?: boolean;
    declined?: boolean;
    ultimatum?: boolean;
    ultimatumTarget?: string;
  };
  elimination?: { player: string | null; method: string; tally: Record<string, number> };
  murder?: { victim?: string | null; skipped?: boolean };
  banishmentSkipped?: boolean;
  finaleStarted?: boolean;
  shieldBlocked?: boolean;
  daggerUsed?: boolean;
}

export interface Quality {
  hallucination_score?: number;
  diversity?: { duplication_score?: number; duplicate_rate?: number; distinct_texts?: number };
  secrecy?: { flags_total?: number; samples?: unknown[] };
  parsing?: { rejected_actions?: number; unparseable_actions?: number };
  private_chats?: {
    reciprocity_rate?: number;
    pairs?: number;
    messages?: number;
    two_way_pairs?: number;
    longest_thread?: number;
    mean_messages_per_pair?: number;
    pairs_by_length?: Record<string, number>;
  };
  speech_similarity?: {
    content_words?: { mean?: number; max?: number; max_pair?: [string, string] };
    phrasing?: { mean?: number; max?: number; max_pair?: [string, string] };
    players_compared?: number;
    pairs?: number;
    per_player?: Record<string, { content_words?: number; phrasing?: number }>;
  };
  messages_checked?: number;
}

export interface PublicMetrics {
  calls: number;
  failedCalls: number;
  providerCalls: number;
  retries: number;
  transportRetries: number;
  parseRetryCalls: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  promptCharsTotal: number;
  byActionType: Record<string, { calls: number; failed: number; input_tokens: number; output_tokens: number }>;
  model?: string;
  provider?: string;
  quality: Quality;
  messages: { public: number; private: number; ratio_public_private?: number };
  events: { total: number; byType: Record<string, number> };
  rejectedActions: number;
  outcomes: Record<string, string>;
  survivingTraitors: string[];
  winner?: string;
  winningTeam?: string;
  finale: boolean;
  soloTraitorWin: boolean;
  seed?: number;
  experimentId?: string;
  rounds?: number;
  players?: number;
  traitors?: number;
}

export interface BenchmarkComponent {
  score?: number | null;
  method?: string;
  run?: unknown;
  season?: unknown;
  [key: string]: unknown;
}

export interface Benchmark {
  season: {
    id: string;
    name: string;
    winner_faction: string;
    final_counts: Record<string, number>;
    cast: { id: string; role: string; traitor_type?: string | null; exit_episode?: number }[];
    eliminations: { episode: number; night_victim?: string | null; banishment: string[] }[];
    recruitments: { episode: number; target: string; accepted: boolean }[];
    sources: string[];
  };
  result: {
    overall: { score: number; weights: Record<string, number>; components_scored: string[]; components_skipped: string[] };
    [key: string]: BenchmarkComponent | unknown;
  };
  overall?: number;
}

export interface DiagnosisSection {
  title: string;
  lines: string[];
}

export interface RunSummary {
  id: string;
  slug?: string | null;
  label?: string | null;
  blurb?: string | null;
  game: string;
  season: string;
  family: string;
  flagship: boolean;
  seed?: number;
  holdout: boolean;
  players: Player[];
  rounds: number;
  roundDigests: RoundDigest[];
  outcome: {
    winner?: string;
    winningTeam?: string;
    reason: string;
    finale: boolean;
    solo: boolean;
    survivingTraitors: string[];
  };
  metrics: PublicMetrics;
  benchmark?: Benchmark | null;
  benchmarkScore?: number | null;
  diagnosis?: DiagnosisSection[] | null;
  counts: {
    messages: number;
    public: number;
    private: number;
    traitor: number;
    host: number;
    calls: number;
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
    rejectedActions: number;
  };
  config: {
    name: string;
    players: number;
    traitors: number;
    faithful: number;
    maxRounds: number;
    language: string;
    provider: string;
    model: string;
    modelParameters: Record<string, unknown>;
    flags: Record<string, unknown>;
  };
  hasMessages: boolean;
  hasBenchmark: boolean;
  hasDiagnosis: boolean;
}

export interface Message {
  r: number;
  phase: string;
  channel: Channel;
  from: string;
  to: string[];
  text: string;
}

export interface RunRow {
  id: string;
  slug?: string | null;
  label?: string | null;
  blurb?: string | null;
  game: string;
  season: string;
  family: string;
  flagship: boolean;
  holdout: boolean;
  seed?: number;
  players: number;
  rounds: number;
  traitorCount: number;
  traitors: string[];
  survivors: string[];
  eliminations: { id: string; round: number; method: string }[];
  sampleQuote?: string | null;
  sampleSpeaker?: string | null;
  winner?: string | null;
  winningTeam?: string | null;
  reason?: string | null;
  finale: boolean;
  solo: boolean;
  provider: string;
  model: string;
  calls: number;
  totalTokens: number;
  messages: number;
  traitorMessages: number;
  hallucination?: number | null;
  duplication?: number | null;
  secrecyFlags?: number | null;
  rejectedActions: number;
  reciprocity?: number | null;
  similarityContent?: number | null;
  similarityPhrasing?: number | null;
  benchmarkScore?: number | null;
  hasBenchmark: boolean;
  hasDiagnosis: boolean;
  hasMessages: boolean;
}

export interface Manifest {
  version: number;
  generatedFrom: string[];
  totals: {
    runs: number;
    playerGames: number;
    messages: number;
    calls: number;
    tokens: number;
  };
  models: Record<string, { provider: string; model: string; label: string }>;
  runs: RunRow[];
}
