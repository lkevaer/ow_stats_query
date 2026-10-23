import React, { useState, useEffect, useRef } from 'react';
import { View, Text, ScrollView, Button } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import SearchBar from '@/components/SearchBar';
import LoadingSpinner from '@/components/LoadingSpinner';
import ResultImage from '@/components/ResultImage';
import StatusBadge from '@/components/StatusBadge';
import { useApp } from '@/store/appContext';
import { getRankHistory, getQuickStrength, getCompetitiveStrength, createAnalyzeTask, getTaskStatus } from '@/services/api';

type TabType = 'rank' | 'quick' | 'comp' | 'ai';

const TABS: Array<{ key: TabType; label: string }> = [
  { key: 'rank', label: '段位历史' },
  { key: 'quick', label: '快速强度' },
  { key: 'comp', label: '竞技强度' },
  { key: 'ai', label: 'AI锐评' },
];

export default function AnalyzePage() {
  const { userToken, currentBattletag, ensureToken } = useApp();
  const [loading, setLoading] = useState(false);
  const [currentTab, setCurrentTab] = useState<TabType>('rank');
  const [searchTag, setSearchTag] = useState(currentBattletag);
  const [resultData, setResultData] = useState<{ type: string; content: string; media_type?: string; items?: Array<{ type: string; content: string; media_type?: string }> } | null>(null);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('');
  const [taskId, setTaskId] = useState('');
  const pollingRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
      }
    };
  }, []);

  async function handleSearch(tag: string) {
    setSearchTag(tag);
    setLoading(true);
    setError('');
    setResultData(null);
    setProgress(0);
    setStage('');
    setTaskId('');

    try {
      await ensureToken();
      if (!userToken) {
        Taro.showToast({ title: '获取 token 失败', icon: 'none' });
        setLoading(false);
        return;
      }

      if (currentTab === 'ai') {
        await runAiAnalyze(userToken, tag);
      } else {
        await runNormalQuery(userToken, tag);
      }
    } catch (e) {
      console.error('[Analyze] 查询失败:', e);
      setError('网络错误，请重试');
      setLoading(false);
      Taro.showToast({ title: '网络错误，请重试', icon: 'none' });
    }
  }

  async function runNormalQuery(token: string, tag: string) {
    let response;
    switch (currentTab) {
      case 'rank':
        response = await getRankHistory(token, tag);
        break;
      case 'quick':
        response = await getQuickStrength(token, 5, tag);
        break;
      case 'comp':
        response = await getCompetitiveStrength(token, 5, tag);
        break;
      default:
        response = { success: false, error: '未知类型' };
    }

    if (response.success && response.data) {
      setResultData(response.data);
    } else {
      setError(response.error || '查询失败');
      Taro.showToast({ title: response.error || '查询失败', icon: 'none' });
    }
    setLoading(false);
  }

  async function runAiAnalyze(token: string, tag: string) {
    const createResponse = await createAnalyzeTask('/api/analyze', {
      token,
      battletag: tag,
      index: 1,
    });

    if (!createResponse.success || !createResponse.data?.task_id) {
      setError(createResponse.error || '任务创建失败');
      setLoading(false);
      return;
    }

    const newTaskId = createResponse.data.task_id;
    setTaskId(newTaskId);
    setStage('任务已创建，正在处理…');
    setProgress(10);

    pollingRef.current = setInterval(async () => {
      try {
        const statusResponse = await getTaskStatus(newTaskId);
        if (!statusResponse.success || !statusResponse.data) {
          clearInterval(pollingRef.current!);
          setError('任务查询失败');
          setLoading(false);
          return;
        }

        const task = statusResponse.data;
        setProgress(task.progress_pct || 0);
        setStage(task.stage || '处理中…');

        if (task.status === 'done') {
          clearInterval(pollingRef.current!);
          if (task.result?.success && task.result.data) {
            setResultData(task.result.data);
          } else {
            setError(task.result?.error || '任务执行失败');
          }
          setLoading(false);
        } else if (task.status === 'error') {
          clearInterval(pollingRef.current!);
          setError(task.error || '任务执行错误');
          setLoading(false);
        }
      } catch (e) {
        console.error('[Analyze] 轮询失败:', e);
      }
    }, 2000) as unknown as number;
  }

  function handleTabChange(tab: TabType) {
    setCurrentTab(tab);
    setResultData(null);
    setError('');
    setProgress(0);
    setStage('');
    setTaskId('');
  }

  return (
    <ScrollView className={styles.page} scrollY>
      <View className={styles.searchSection}>
        <SearchBar
          onSearch={handleSearch}
          defaultValue={currentBattletag}
          disabled={loading}
        />
      </View>

      <View className={styles.tabBar}>
        {TABS.map((tab) => (
          <Button
            key={tab.key}
            className={`${styles.tab} ${currentTab === tab.key ? styles.active : ''}`}
            onClick={() => handleTabChange(tab.key)}
          >
            {tab.label}
          </Button>
        ))}
      </View>

      {currentTab === 'ai' && (
        <View className={styles.inputSection}>
          <View className={styles.inputWrap}>
            <Text className={styles.label}>对局序号：</Text>
            <Text className={styles.input}>1</Text>
          </View>
        </View>
      )}

      <View className={styles.actionButton}>
        <Button className={styles.button} onClick={() => handleSearch(searchTag || currentBattletag)} disabled={loading || !searchTag && !currentBattletag}>
          {loading ? '分析中…' : '开始分析'}
        </Button>
      </View>

      {loading && taskId && (
        <View className={styles.progressSection}>
          <Text className={styles.pct}>{progress}%</Text>
          <View className={styles.progressBar}>
            <View className={styles.fill} style={{ width: `${progress}%` }}></View>
          </View>
          <Text className={styles.stage}>{stage}</Text>
        </View>
      )}

      {loading && !taskId && <LoadingSpinner text="正在分析…" />}

      {error && !loading && (
        <View className={styles.resultSection}>
          <View className={styles.card}>
            <View className={styles.cardHeader}>
              <Text className={styles.title}>分析结果</Text>
              <StatusBadge type="error" text="失败" />
            </View>
            <View className={styles.cardBody}>
              <Text className={styles.textResult}>{error}</Text>
            </View>
          </View>
        </View>
      )}

      {resultData && !loading && (
        <View className={styles.resultSection}>
          <View className={styles.card}>
            <View className={styles.cardHeader}>
              <Text className={styles.title}>分析结果</Text>
              <StatusBadge type="success" text="完成" />
            </View>
            <View className={styles.cardBody}>
              {resultData.type === 'multi' && resultData.items ? (
                <>
                  {resultData.items.map((item, index) => (
                    <View key={index} style={{ marginBottom: index < resultData.items!.length - 1 ? '24rpx' : '0' }}>
                      {item.type === 'image' && item.content ? (
                        <ResultImage base64={item.content} mediaType={item.media_type} />
                      ) : (
                        <Text className={styles.textResult}>{item.content}</Text>
                      )}
                    </View>
                  ))}
                </>
              ) : resultData.type === 'image' && resultData.content ? (
                <ResultImage base64={resultData.content} mediaType={resultData.media_type} />
              ) : (
                <Text className={styles.textResult}>{resultData.content}</Text>
              )}
            </View>
          </View>
        </View>
      )}

      {!loading && !error && !resultData && (
        <View className={styles.emptyState}>
          <Text className={styles.icon}>🤖</Text>
          <Text className={styles.text}>选择分析类型开始分析</Text>
        </View>
      )}
    </ScrollView>
  );
}