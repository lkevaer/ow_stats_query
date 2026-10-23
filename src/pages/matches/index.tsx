import React, { useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import SearchBar from '@/components/SearchBar';
import LoadingSpinner from '@/components/LoadingSpinner';
import ResultImage from '@/components/ResultImage';
import StatusBadge from '@/components/StatusBadge';
import { useApp } from '@/store/appContext';
import { getMatches } from '@/services/api';

const LIMIT_OPTIONS = [5, 10, 15, 20];

export default function MatchesPage() {
  const { userToken, currentBattletag, ensureToken } = useApp();
  const [loading, setLoading] = useState(false);
  const [matchesData, setMatchesData] = useState<{ type: string; content: string; matches?: Array<{ matchRet: number; heroGuid: string }>; media_type?: string } | null>(null);
  const [error, setError] = useState('');
  const [searchTag, setSearchTag] = useState(currentBattletag);
  const [limit, setLimit] = useState(5);

  async function handleSearch(tag: string) {
    setSearchTag(tag);
    setLoading(true);
    setError('');
    setMatchesData(null);

    try {
      await ensureToken();
      if (!userToken) {
        Taro.showToast({ title: '获取 token 失败', icon: 'none' });
        return;
      }

      const response = await getMatches(userToken, tag, limit);
      if (response.success && response.data) {
        setMatchesData(response.data);
      } else {
        setError(response.error || '查询失败');
        Taro.showToast({ title: response.error || '查询失败', icon: 'none' });
      }
    } catch (e) {
      console.error('[Matches] 查询失败:', e);
      setError('网络错误，请重试');
      Taro.showToast({ title: '网络错误，请重试', icon: 'none' });
    } finally {
      setLoading(false);
    }
  }

  function handleLimitChange(newLimit: number) {
    setLimit(newLimit);
    if (searchTag) {
      handleSearch(searchTag);
    }
  }

  function handleMatchTap(index: number) {
    Taro.navigateTo({
      url: `/pages/match-detail/index?index=${index}&battletag=${encodeURIComponent(searchTag || currentBattletag)}`,
    });
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

      <View className={styles.filterBar}>
        {LIMIT_OPTIONS.map((num) => (
          <Button
            key={num}
            className={`${styles.filterItem} ${limit === num ? styles.active : ''}`}
            onClick={() => handleLimitChange(num)}
          >
            {num}场
          </Button>
        ))}
      </View>

      {loading ? (
        <LoadingSpinner text="正在查询战绩…" />
      ) : error ? (
        <View className={styles.resultCard}>
          <View className={styles.cardHeader}>
            <Text className={styles.title}>战绩查询</Text>
            <StatusBadge type="error" text="失败" />
          </View>
          <View className={styles.cardBody}>
            <Text style={{ color: '#ff5b5b', fontSize: '24rpx' }}>{error}</Text>
          </View>
        </View>
      ) : matchesData ? (
        <>
          {matchesData.type === 'image' && matchesData.content ? (
            <View className={styles.resultCard}>
              <View className={styles.cardHeader}>
                <Text className={styles.title}>最近战绩</Text>
                <StatusBadge type="success" text={`${matchesData.matches?.length || 0}场`} />
              </View>
              <View className={styles.cardBody}>
                <ResultImage base64={matchesData.content} mediaType={matchesData.media_type} />
              </View>
            </View>
          ) : (
            <View className={styles.matchList}>
              {matchesData.matches?.map((match, index) => (
                <View key={index} className={styles.matchItem} onClick={() => handleMatchTap(index + 1)}>
                  <View className={`${styles.result} ${match.matchRet === 1 ? styles.win : styles.lose}`}>
                    <Text>{match.matchRet === 1 ? '胜' : '败'}</Text>
                  </View>
                  <View className={styles.info}>
                    <Text className={styles.hero}>{match.heroGuid || '未知英雄'}</Text>
                    <Text className={styles.meta}>第 {index + 1} 场</Text>
                  </View>
                  <Text className={styles.arrow}>›</Text>
                </View>
              ))}
            </View>
          )}
        </>
      ) : (
        <View className={styles.emptyState}>
          <Text className={styles.icon}>📋</Text>
          <Text className={styles.text}>输入 BattleTag 查询战绩</Text>
        </View>
      )}
    </ScrollView>
  );
}