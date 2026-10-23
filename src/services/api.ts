import Taro from '@tarojs/taro';
import type { ApiResponse, BindingRecord, PlayerProfile, MatchList, TaskStatus, AnalysisResult } from '@/types';

const BASE_URL = '/api';

async function request<T>(
  url: string,
  method: 'GET' | 'POST' = 'GET',
  data?: Record<string, any>,
  headers: Record<string, string> = {}
): Promise<ApiResponse<T>> {
  try {
    const response = await Taro.request({
      url: `${BASE_URL}${url}`,
      method,
      data,
      header: {
        'Content-Type': 'application/json',
        ...headers,
      },
      timeout: 30000,
    });

    if (response.statusCode >= 400) {
      return {
        success: false,
        error: response.data?.error || `请求失败 (${response.statusCode})`,
        status_code: response.statusCode,
      };
    }

    return response.data as ApiResponse<T>;
  } catch (error) {
    console.error('[API] 请求失败:', error);
    return {
      success: false,
      error: error instanceof Error ? error.message : '网络请求失败',
      status_code: 500,
    };
  }
}

export async function generateToken(): Promise<ApiResponse<{ token: string }>> {
  return request('/token/new');
}

export async function bindAccount(
  token: string,
  battletag: string
): Promise<ApiResponse<BindingRecord>> {
  return request(`/bind?token=${encodeURIComponent(token)}&battletag=${encodeURIComponent(battletag)}`, 'POST');
}

export async function unbindAccount(token: string): Promise<ApiResponse> {
  return request(`/unbind?token=${encodeURIComponent(token)}`, 'POST');
}

export async function getBinding(token: string): Promise<ApiResponse<BindingRecord>> {
  return request(`/my-bind?token=${encodeURIComponent(token)}`);
}

export async function refreshBindingToken(token: string): Promise<ApiResponse<BindingRecord>> {
  return request(`/refresh-token?token=${encodeURIComponent(token)}`, 'POST');
}

export async function getProfile(
  token: string,
  battletag?: string
): Promise<ApiResponse<PlayerProfile>> {
  let url = `/profile?token=${encodeURIComponent(token)}`;
  if (battletag) {
    url += `&battletag=${encodeURIComponent(battletag)}`;
  }
  return request(url);
}

export async function getMatches(
  token: string,
  battletag?: string,
  limit: number = 5
): Promise<ApiResponse<MatchList>> {
  let url = `/matches?token=${encodeURIComponent(token)}&limit=${limit}`;
  if (battletag) {
    url += `&battletag=${encodeURIComponent(battletag)}`;
  }
  return request(url);
}

export async function getMatchDetail(
  token: string,
  index: number = 1,
  battletag?: string
): Promise<ApiResponse<PlayerProfile>> {
  let url = `/match-detail?token=${encodeURIComponent(token)}&index=${index}`;
  if (battletag) {
    url += `&battletag=${encodeURIComponent(battletag)}`;
  }
  return request(url);
}

export async function createAnalyzeTask(
  endpoint: string,
  params: Record<string, any>
): Promise<ApiResponse<{ task_id: string }>> {
  return request('/task/create', 'POST', { endpoint, params });
}

export async function getTaskStatus(taskId: string): Promise<ApiResponse<TaskStatus>> {
  return request(`/task/${taskId}`);
}

export async function analyzeMatch(
  token: string,
  index: number = 1,
  battletag?: string
): Promise<ApiResponse<AnalysisResult>> {
  let url = `/analyze?token=${encodeURIComponent(token)}&index=${index}`;
  if (battletag) {
    url += `&battletag=${encodeURIComponent(battletag)}`;
  }
  return request(url);
}

export async function getRankHistory(
  token: string,
  battletag?: string
): Promise<ApiResponse<PlayerProfile>> {
  let url = `/rank-history?token=${encodeURIComponent(token)}`;
  if (battletag) {
    url += `&battletag=${encodeURIComponent(battletag)}`;
  }
  return request(url);
}

export async function getQuickStrength(
  token: string,
  limit: number = 5,
  battletag?: string
): Promise<ApiResponse<{ type: 'image' | 'text'; content: string }>> {
  let url = `/quick-strength?token=${encodeURIComponent(token)}&limit=${limit}`;
  if (battletag) {
    url += `&battletag=${encodeURIComponent(battletag)}`;
  }
  return request(url);
}

export async function getCompetitiveStrength(
  token: string,
  limit: number = 5,
  battletag?: string
): Promise<ApiResponse<{ type: 'image' | 'text'; content: string }>> {
  let url = `/competitive-strength?token=${encodeURIComponent(token)}&limit=${limit}`;
  if (battletag) {
    url += `&battletag=${encodeURIComponent(battletag)}`;
  }
  return request(url);
}

export async function getHeroPickRate(
  mode: 'competitive' | 'quick' = 'competitive',
  rank: string = 'all'
): Promise<ApiResponse<{ type: 'image' | 'text'; content: string }>> {
  return request(`/hero-pick-rate?mode=${mode}&rank=${rank}`);
}

export async function getShop(): Promise<ApiResponse<{ type: 'image' | 'text'; content: string }>> {
  return request('/shop');
}

export async function getPatchNotes(
  kind: 'latest' | 'small' | 'big' = 'latest'
): Promise<ApiResponse<{ type: 'image' | 'text'; content: string }>> {
  return request(`/patch-notes?kind=${kind}`);
}

export async function searchPlayer(keyword: string): Promise<ApiResponse> {
  return request(`/search-player?keyword=${encodeURIComponent(keyword)}`);
}