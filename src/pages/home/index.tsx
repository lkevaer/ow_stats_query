import React, { useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import SearchBar from '@/components/SearchBar';
import LoadingSpinner from '@/components/LoadingSpinner';
import ResultImage from '@/components/ResultImage';
import StatusBadge from '@/components/StatusBadge';
import { useApp } from '@/store/appContext';
import { getProfile } from '@/services/api';

const ACTIONS = [
  { icon: '📊', label: '资料', key: 'profile' },
  { icon: '📋', label: '战绩', key: 'matches' },
  { icon: '🏆', label: '段位', key: 'rank' },
  { icon: '⚡', label: '强度', key: 'strength' },
  { icon: '🤖', label: '锐评', key: 'analyze' },
  { icon: '🔍', label: '同玩', key: 'sameplay' },
  { icon: '🛒', label: '商店', key: 'shop' },
  { icon: '📝', label: '补丁', key: 'patch' },
];

export default function HomePage() {
  const { userToken, binding, currentBattletag, ensureToken } = useApp();
  const [loading, setLoading] = useState(false);
  const [profileData, setProfileData] = useState<{ type: string; content: string; media_type?: string } | null>(null);
  const [error, setError] = useState('');
  const [searchTag, setSearchTag] = useState('');

  async function handleSearch(tag: string) {
    setSearchTag(tag);
    setLoading(true);
    setError('');
    setProfileData(null);

    try {
      await ensureToken();
      if (!userToken) {
        Taro.showToast({ title: '获取 token 失败', icon: 'none' });
        return;
      }

      const response = await getProfile(userToken, tag);
      if (response.success && response.data) {
        setProfileData(response.data);
      } else {
        setError(response.error || '查询失败');
        Taro.showToast({ title: response.error || '查询失败', icon: 'none' });
      }
    } catch (e) {
      console.error('[Home] 查询失败:', e);
      setError('网络错误，请重试');
      Taro.showToast({ title: '网络错误，请重试', icon: 'none' });
    } finally {
      setLoading(false);
    }
  }

  function handleAction(action: typeof ACTIONS[0]) {
    if (action.key === 'matches') {
      Taro.switchTab({ url: '/pages/matches/index' });
    } else if (action.key === 'analyze') {
      Taro.switchTab({ url: '/pages/analyze/index' });
    } else if (action.key === 'profile') {
      if (currentBattletag) {
        handleSearch(currentBattletag);
      } else {
        Taro.showToast({ title: '请先绑定或输入账号', icon: 'none' });
      }
    } else {
      Taro.showToast({ title: `${action.label}功能开发中`, icon: 'none' });
    }
  }

  return (
    <ScrollView className={styles.page} scrollY>
      <View className={styles.header}>
        <View className={styles.title}>
          <View className={styles.dot}></View>
          <Text>守望先锋数据</Text>
        </View>
      </View>

      <View className={styles.searchSection}>
        <SearchBar
          onSearch={handleSearch}
          defaultValue={currentBattletag}
          disabled={loading}
        />
      </View>

      <View className={styles.bindingInfo}>
        {binding ? (
          <View className={styles.content}>
            <View className={styles.left}>
              <View className={styles.avatar}>🎮</View>
              <View className={styles.info}>
                <Text className={styles.name}>{binding.display_name}</Text>
                <Text className={styles.hint}>已绑定账号</Text>
              </View>
            </View>
            <Text className={styles.action}>查看详情</Text>
          </View>
        ) : (
          <View className={styles.content}>
            <View className={styles.left}>
              <View className={styles.avatar}>👤</View>
              <View className={styles.info}>
                <Text className={styles.name}>未绑定账号</Text>
                <Text className={styles.hint}>绑定后可直接查询</Text>
              </View>
            </View>
            <Text className={styles.action} onClick={() => Taro.switchTab({ url: '/pages/mine/index' })}>
              去绑定
            </Text>
          </View>
        )}
      </View>

      <View className={styles.quickActions}>
        <Text className={styles.title}>快速查询</Text>
        <View className={styles.grid}>
          {ACTIONS.map((action) => (
            <View key={action.key} className={styles.item} onClick={() => handleAction(action)}>
              <Text className={styles.icon}>{action.icon}</Text>
              <Text className={styles.label}>{action.label}</Text>
            </View>
          ))}
        </View>
      </View>

      <View className={styles.resultSection}>
        {loading ? (
          <LoadingSpinner text="正在查询玩家资料…" />
        ) : error ? (
          <View className={styles.card}>
            <View className={styles.cardHeader}>
              <Text className={styles.title}>查询结果</Text>
              <StatusBadge type="error" text="失败" />
            </View>
            <View className={styles.cardBody}>
              <Text className={styles.textResult}>{error}</Text>
            </View>
          </View>
        ) : profileData ? (
          <View className={styles.card}>
            <View className={styles.cardHeader}>
              <Text className={styles.title}>玩家资料</Text>
              <StatusBadge type="success" text="成功" />
            </View>
            <View className={styles.cardBody}>
              {profileData.type === 'image' && profileData.content ? (
                <ResultImage base64={profileData.content} mediaType={profileData.media_type} />
              ) : (
                <Text className={styles.textResult}>{profileData.content}</Text>
              )}
            </View>
          </View>
        ) : (
          <View className={styles.emptyState}>
            <Text className={styles.icon}>🎯</Text>
            <Text className={styles.text}>输入 BattleTag 开始查询</Text>
          </View>
        )}
      </View>
    </ScrollView>
  );
}