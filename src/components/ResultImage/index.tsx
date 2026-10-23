import React from 'react';
import { View, Image } from '@tarojs/components';
import styles from './index.module.scss';

interface ResultImageProps {
  base64: string;
  mediaType?: string;
}

export default function ResultImage({ base64, mediaType = 'image/png' }: ResultImageProps) {
  const src = `data:${mediaType};base64,${base64}`;

  return (
    <View className={styles.container}>
      <Image className={styles.image} src={src} mode="widthFix" />
    </View>
  );
}