export interface ApiResponse<T = any> {
  success: boolean;
  data?: T;
  error?: string;
  status_code?: number;
}

export interface BindingRecord {
  battletag: string;
  customer_token: string;
  bnet_id: string;
  display_name: string;
  icon_url: string;
  customer_token_resolved: boolean;
}

export interface PlayerProfile {
  type: 'image' | 'text';
  content: string;
  media_type?: string;
}

export interface MatchItem {
  matchRet: number;
  heroGuid: string;
  time?: string;
  map?: string;
}

export interface MatchList {
  type: 'image' | 'text';
  content: string;
  matches?: MatchItem[];
  media_type?: string;
}

export interface TaskStatus {
  task_id: string;
  status: 'pending' | 'running' | 'done' | 'error';
  progress_pct: number;
  stage: string;
  result?: ApiResponse;
  error?: string;
}

export interface AnalysisResult {
  type: 'multi';
  items: Array<{
    type: 'image' | 'text';
    content: string;
    media_type?: string;
  }>;
  ai_enabled: boolean;
}

export interface RankHistoryItem {
  season: string;
  competitive: {
    roles: Array<{
      role_type: string;
      current: { rank_score: number; rank_sub_tier: number };
      peak: { rank_score: number; rank_sub_tier: number };
      match_sum: number;
      win_rate: number;
    }>;
  };
}

export interface AppState {
  userToken: string;
  binding: BindingRecord | null;
  currentBattletag: string;
  lastQueryTime: number;
}

export interface StrengthResult {
  type: 'image' | 'text';
  content: string;
  summary?: {
    overall_avg_score: string;
    overall_avg_rank: string;
  };
  media_type?: string;
}