import React from 'react';
import { View, Text, Button } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';

export default function MatchDetailPage() {
  function handleBack() {
    Taro.navigateBack();
  }

  return (
    <View className={styles.page}>
      <Text className={styles.icon}>🎯</Text>
      <Text className={styles.title}>对局详情</Text>
      <Text className={styles.text}>功能正在开发中，敬请期待！</Text>
      <Button className={styles.button} onClick={handleBack}>返回</Button>
    </View>
  );
}