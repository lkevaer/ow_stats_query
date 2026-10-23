import React, { useState } from 'react';
import { View, Text, ScrollView, Button, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import StatusBadge from '@/components/StatusBadge';
import { useApp } from '@/store/appContext';
import { bindAccount, unbindAccount, refreshBindingToken } from '@/services/api';

const MENU_ITEMS = [
  { icon: '🔄', text: '刷新绑定', key: 'refresh' },
  { icon: '📖', text: '使用说明', key: 'help' },
  { icon: 'ℹ️', text: '关于', key: 'about' },
];

export default function MinePage() {
  const { userToken, binding, ensureToken, setBinding, clearBinding } = useApp();
  const [inputTag, setInputTag] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleBind() {
    const tag = inputTag.trim();
    if (!tag || !tag.includes('#')) {
      Taro.showToast({ title: '请输入正确的 BattleTag（Name#1234）', icon: 'none' });
      return;
    }

    setLoading(true);
    try {
      await ensureToken();
      if (!userToken) {
        Taro.showToast({ title: '获取 token 失败', icon: 'none' });
        return;
      }

      const response = await bindAccount(userToken, tag);
      if (response.success && response.data) {
        setBinding(response.data);
        setInputTag('');
        Taro.showToast({ title: `已绑定 ${response.data.display_name}`, icon: 'success' });
      } else {
        Taro.showToast({ title: response.error || '绑定失败', icon: 'none' });
      }
    } catch (e) {
      console.error('[Mine] 绑定失败:', e);
      Taro.showToast({ title: '网络错误，请重试', icon: 'none' });
    } finally {
      setLoading(false);
    }
  }

  async function handleUnbind() {
    Taro.showModal({
      title: '确认解绑',
      content: '确定要解绑当前账号吗？',
      success: async (res) => {
        if (!res.confirm) return;

        try {
          const response = await unbindAccount(userToken);
          if (response.success) {
            clearBinding();
            Taro.showToast({ title: '已解绑', icon: 'success' });
          } else {
            Taro.showToast({ title: response.error || '解绑失败', icon: 'none' });
          }
        } catch (e) {
          console.error('[Mine] 解绑失败:', e);
          Taro.showToast({ title: '网络错误，请重试', icon: 'none' });
        }
      },
    });
  }

  async function handleRefresh() {
    if (!binding) {
      Taro.showToast({ title: '请先绑定账号', icon: 'none' });
      return;
    }

    setLoading(true);
    try {
      const response = await refreshBindingToken(userToken);
      if (response.success && response.data) {
        setBinding(response.data);
        Taro.showToast({ title: '绑定已刷新', icon: 'success' });
      } else {
        Taro.showToast({ title: response.error || '刷新失败', icon: 'none' });
      }
    } catch (e) {
      console.error('[Mine] 刷新失败:', e);
      Taro.showToast({ title: '网络错误，请重试', icon: 'none' });
    } finally {
      setLoading(false);
    }
  }

  function handleMenu(item: typeof MENU_ITEMS[0]) {
    if (item.key === 'refresh') {
      handleRefresh();
    } else if (item.key === 'help') {
      Taro.showModal({
        title: '使用说明',
        content: '1. 在首页输入 BattleTag 进行查询\n2. 绑定账号后可直接查询，无需重复输入\n3. AI锐评功能需要一定时间处理，请耐心等待',
        showCancel: false,
      });
    } else if (item.key === 'about') {
      Taro.showModal({
        title: '关于',
        content: '守望先锋数据查询 v2.0\n\n基于网易大神 API 开发\n支持玩家资料、战绩查询、AI锐评等功能',
        showCancel: false,
      });
    }
  }

  return (
    <ScrollView className={styles.page} scrollY>
      <View className={styles.userCard}>
        <View className={styles.avatarRow}>
          <View className={styles.avatar}>{binding ? '🎮' : '👤'}</View>
          <View className={styles.info}>
            <Text className={styles.name}>{binding ? binding.display_name : '未登录'}</Text>
            <Text className={styles.token}>{userToken ? `Token: ${userToken.slice(0, 8)}...` : '未获取 Token'}</Text>
          </View>
        </View>

        {binding && (
          <View className={styles.bindingInfo}>
            <Text className={styles.title}>绑定账号</Text>
            <View style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text className={styles.value}>{binding.battletag}</Text>
              <StatusBadge type={binding.customer_token_resolved ? 'success' : 'warning'} text={binding.customer_token_resolved ? '已解析' : '待解析'} />
            </View>
          </View>
        )}
      </View>

      <View className={styles.bindSection}>
        {binding ? (
          <>
            <Text className={styles.title}>已绑定账号：{binding.battletag}</Text>
            <Button className={styles.unbindButton} onClick={handleUnbind} disabled={loading}>
              解绑账号
            </Button>
          </>
        ) : (
          <>
            <Text className={styles.title}>绑定账号</Text>
            <View className={styles.inputWrap}>
              <Input
                className={styles.input}
                placeholder="请输入 BattleTag（如 Name#1234）"
                placeholderStyle="color: #5a6478"
                value={inputTag}
                onInput={(e) => setInputTag(e.detail.value)}
              />
            </View>
            <Button className={styles.button} onClick={handleBind} disabled={loading || !inputTag.trim()}>
              {loading ? '绑定中…' : '绑定'}
            </Button>
          </>
        )}
      </View>

      <View className={styles.section}>
        <Text className={styles.title}>功能菜单</Text>
        <View className={styles.list}>
          {MENU_ITEMS.map((item) => (
            <View key={item.key} className={styles.item} onClick={() => handleMenu(item)}>
              <View className={styles.left}>
                <Text className={styles.icon}>{item.icon}</Text>
                <Text className={styles.text}>{item.text}</Text>
              </View>
              <View className={styles.right}>
                <Text className={styles.arrow}>›</Text>
              </View>
            </View>
          ))}
        </View>
      </View>
    </ScrollView>
  );
}