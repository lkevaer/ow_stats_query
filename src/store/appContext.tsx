import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import type { BindingRecord } from '@/types';
import { generateToken, getBinding } from '@/services/api';

const STORAGE_TOKEN_KEY = 'ow_user_token';
const STORAGE_BIND_TAG_KEY = 'ow_user_battletag';

interface AppContextType {
  userToken: string;
  binding: BindingRecord | null;
  currentBattletag: string;
  ensureToken: () => Promise<void>;
  setBinding: (binding: BindingRecord | null) => void;
  setCurrentBattletag: (tag: string) => void;
  clearBinding: () => void;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

export function AppProvider({ children }: { children: ReactNode }) {
  const [userToken, setUserToken] = useState('');
  const [binding, setBinding] = useState<BindingRecord | null>(null);
  const [currentBattletag, setCurrentBattletag] = useState('');

  useEffect(() => {
    const storedToken = Taro.getStorageSync(STORAGE_TOKEN_KEY) || '';
    const storedTag = Taro.getStorageSync(STORAGE_BIND_TAG_KEY) || '';
    setUserToken(storedToken);
    setCurrentBattletag(storedTag);

    if (storedToken) {
      loadBinding(storedToken);
    }
  }, []);

  async function loadBinding(token: string) {
    try {
      const response = await getBinding(token);
      if (response.success && response.data) {
        setBinding(response.data);
        if (response.data.display_name) {
          setCurrentBattletag(response.data.display_name);
          Taro.setStorageSync(STORAGE_BIND_TAG_KEY, response.data.display_name);
        }
      }
    } catch (error) {
      console.error('[AppContext] 加载绑定失败:', error);
    }
  }

  const ensureToken = useCallback(async () => {
    if (userToken) return;

    try {
      const response = await generateToken();
      if (response.success && response.data?.token) {
        const newToken = response.data.token;
        setUserToken(newToken);
        Taro.setStorageSync(STORAGE_TOKEN_KEY, newToken);
      }
    } catch (error) {
      console.error('[AppContext] 生成 token 失败:', error);
    }
  }, [userToken]);

  const handleSetBinding = useCallback((newBinding: BindingRecord | null) => {
    setBinding(newBinding);
    if (newBinding && newBinding.display_name) {
      setCurrentBattletag(newBinding.display_name);
      Taro.setStorageSync(STORAGE_BIND_TAG_KEY, newBinding.display_name);
    }
  }, []);

  const handleSetCurrentBattletag = useCallback((tag: string) => {
    setCurrentBattletag(tag);
    Taro.setStorageSync(STORAGE_BIND_TAG_KEY, tag);
  }, []);

  const handleClearBinding = useCallback(() => {
    setBinding(null);
    setCurrentBattletag('');
    Taro.removeStorageSync(STORAGE_BIND_TAG_KEY);
  }, []);

  return (
    <AppContext.Provider
      value={{
        userToken,
        binding,
        currentBattletag,
        ensureToken,
        setBinding: handleSetBinding,
        setCurrentBattletag: handleSetCurrentBattletag,
        clearBinding: handleClearBinding,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within an AppProvider');
  }
  return context;
}