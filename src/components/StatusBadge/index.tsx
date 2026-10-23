import React from 'react';
import { View, Text } from '@tarojs/components';
import styles from './index.module.scss';

type StatusType = 'success' | 'error' | 'warning' | 'info' | 'default';

interface StatusBadgeProps {
  type?: StatusType;
  text: string;
}

export default function StatusBadge({ type = 'default', text }: StatusBadgeProps) {
  return (
    <View className={`${styles.badge} ${styles[type]}`}>
      <Text className={styles.text}>{text}</Text>
    </View>
  );
}