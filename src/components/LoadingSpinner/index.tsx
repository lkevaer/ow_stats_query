import React from 'react';
import { View, Text } from '@tarojs/components';
import styles from './index.module.scss';

interface LoadingSpinnerProps {
  text?: string;
  size?: 'small' | 'medium' | 'large';
}

export default function LoadingSpinner({ text = '加载中…', size = 'medium' }: LoadingSpinnerProps) {
  return (
    <View className={styles.container}>
      <View className={`${styles.spinner} ${styles[size]}`}></View>
      {text && <Text className={styles.text}>{text}</Text>}
    </View>
  );
}