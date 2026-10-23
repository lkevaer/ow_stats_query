import React, { useState, useRef, useEffect } from 'react';
import { View, Text, Input, Button } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';

interface SearchBarProps {
  onSearch: (value: string) => void;
  placeholder?: string;
  defaultValue?: string;
  disabled?: boolean;
}

export default function SearchBar({ onSearch, placeholder = '请输入 BattleTag（如 Name#1234）', defaultValue = '', disabled = false }: SearchBarProps) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<any>(null);

  useEffect(() => {
    setValue(defaultValue);
  }, [defaultValue]);

  function handleInput(e: any) {
    setValue(e.detail.value);
  }

  function handleSearch() {
    const tag = value.trim();
    if (!tag) {
      Taro.showToast({ title: '请输入 BattleTag', icon: 'none' });
      return;
    }
    if (!tag.includes('#')) {
      Taro.showToast({ title: '格式错误，请输入 Name#1234', icon: 'none' });
      return;
    }
    onSearch(tag);
  }

  function handleConfirm(e: any) {
    handleSearch();
  }

  return (
    <View className={styles.searchBar}>
      <View className={styles.inputWrap}>
        <Text className={styles.icon}>🔍</Text>
        <Input
          ref={inputRef}
          className={styles.input}
          placeholder={placeholder}
          placeholderClass={styles.placeholder}
          value={value}
          onInput={handleInput}
          onConfirm={handleConfirm}
          disabled={disabled}
          confirmType="search"
        />
      </View>
      <Button className={styles.button} onClick={handleSearch} disabled={disabled || !value.trim()}>
        查询
      </Button>
    </View>
  );
}