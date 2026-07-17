import React, { createContext, useContext, useEffect, useState, ReactNode } from 'react';

// 预设皮肤定义
export interface SkinPreset {
  id: string;
  name: string;
  // 主色调
  primary: string;
  primaryHover: string;
  primaryLight: string;
  primaryLighter: string;
  // 渐变色
  gradientFrom: string;
  gradientTo: string;
  // 侧边栏
  sidebarBg: string;
  sidebarText: string;
  sidebarActiveBg: string;
  sidebarHoverBg: string;
  sidebarBorder: string;
  // 按钮阴影
  shadowColor: string;
}

const SKIN_PRESETS: Record<string, SkinPreset> = {
  ocean: {
    id: 'ocean',
    name: '海洋蓝',
    primary: '#3b82f6',
    primaryHover: '#2563eb',
    primaryLight: '#dbeafe',
    primaryLighter: '#eff6ff',
    gradientFrom: '#3b82f6',
    gradientTo: '#6366f1',
    sidebarBg: '#0f172a',
    sidebarText: '#cbd5e1',
    sidebarActiveBg: '#3b82f6',
    sidebarHoverBg: '#1e293b',
    sidebarBorder: '#1e293b',
    shadowColor: '59, 130, 246',
  },
  forest: {
    id: 'forest',
    name: '森林绿',
    primary: '#22c55e',
    primaryHover: '#16a34a',
    primaryLight: '#dcfce7',
    primaryLighter: '#f0fdf4',
    gradientFrom: '#22c55e',
    gradientTo: '#10b981',
    sidebarBg: '#14241a',
    sidebarText: '#86efac',
    sidebarActiveBg: '#22c55e',
    sidebarHoverBg: '#1a3a28',
    sidebarBorder: '#1a3a28',
    shadowColor: '34, 197, 94',
  },
  twilight: {
    id: 'twilight',
    name: '暮光紫',
    primary: '#8b5cf6',
    primaryHover: '#7c3aed',
    primaryLight: '#ede9fe',
    primaryLighter: '#f5f3ff',
    gradientFrom: '#8b5cf6',
    gradientTo: '#a855f7',
    sidebarBg: '#1a1033',
    sidebarText: '#c4b5fd',
    sidebarActiveBg: '#8b5cf6',
    sidebarHoverBg: '#2d1b4e',
    sidebarBorder: '#2d1b4e',
    shadowColor: '139, 92, 246',
  },
  sunset: {
    id: 'sunset',
    name: '落日橙',
    primary: '#f59e0b',
    primaryHover: '#d97706',
    primaryLight: '#fef3c7',
    primaryLighter: '#fffbeb',
    gradientFrom: '#f59e0b',
    gradientTo: '#f97316',
    sidebarBg: '#1c130a',
    sidebarText: '#fcd34d',
    sidebarActiveBg: '#f59e0b',
    sidebarHoverBg: '#2e1d0f',
    sidebarBorder: '#2e1d0f',
    shadowColor: '245, 158, 11',
  },
  rose: {
    id: 'rose',
    name: '玫瑰红',
    primary: '#ef4444',
    primaryHover: '#dc2626',
    primaryLight: '#fee2e2',
    primaryLighter: '#fef2f2',
    gradientFrom: '#ef4444',
    gradientTo: '#ec4899',
    sidebarBg: '#1a0f0f',
    sidebarText: '#fca5a5',
    sidebarActiveBg: '#ef4444',
    sidebarHoverBg: '#2e1717',
    sidebarBorder: '#2e1717',
    shadowColor: '239, 68, 68',
  },
  cyan: {
    id: 'cyan',
    name: '青碧蓝',
    primary: '#06b6d4',
    primaryHover: '#0891b2',
    primaryLight: '#cffafe',
    primaryLighter: '#ecfeff',
    gradientFrom: '#06b6d4',
    gradientTo: '#0ea5e9',
    sidebarBg: '#0a1a1f',
    sidebarText: '#67e8f9',
    sidebarActiveBg: '#06b6d4',
    sidebarHoverBg: '#0f2e36',
    sidebarBorder: '#0f2e36',
    shadowColor: '6, 182, 212',
  },
};

interface ThemeContextType {
  skinId: string;
  skin: SkinPreset;
  mode: 'light' | 'dark' | 'system';
  setSkinId: (id: string) => void;
  setMode: (mode: 'light' | 'dark' | 'system') => void;
  isDark: boolean;
  skins: SkinPreset[];
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};

export const ThemeProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [skinId, setSkinIdState] = useState('ocean');
  const [mode, setModeState] = useState<'light' | 'dark' | 'system'>('light');

  useEffect(() => {
    const savedSkin = localStorage.getItem('skinId');
    const savedMode = localStorage.getItem('themeMode');
    if (savedSkin && SKIN_PRESETS[savedSkin]) setSkinIdState(savedSkin);
    if (savedMode) setModeState(savedMode as 'light' | 'dark' | 'system');
  }, []);

  const skin = SKIN_PRESETS[skinId] || SKIN_PRESETS.ocean;
  const isDark = mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  useEffect(() => {
    const root = document.documentElement;

    // 设置主题色 CSS 变量
    root.style.setProperty('--color-primary', skin.primary);
    root.style.setProperty('--color-primary-hover', skin.primaryHover);
    root.style.setProperty('--color-primary-light', skin.primaryLight);
    root.style.setProperty('--color-primary-lighter', skin.primaryLighter);
    root.style.setProperty('--color-gradient-from', skin.gradientFrom);
    root.style.setProperty('--color-gradient-to', skin.gradientTo);
    root.style.setProperty('--color-sidebar-bg', skin.sidebarBg);
    root.style.setProperty('--color-sidebar-text', skin.sidebarText);
    root.style.setProperty('--color-sidebar-active', skin.sidebarActiveBg);
    root.style.setProperty('--color-sidebar-hover', skin.sidebarHoverBg);
    root.style.setProperty('--color-sidebar-border', skin.sidebarBorder);
    root.style.setProperty('--color-shadow-rgb', skin.shadowColor);

    // 深色模式
    if (isDark) {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
  }, [skin, isDark]);

  const setSkinId = (id: string) => {
    setSkinIdState(id);
    localStorage.setItem('skinId', id);
  };

  const setMode = (newMode: 'light' | 'dark' | 'system') => {
    setModeState(newMode);
    localStorage.setItem('themeMode', newMode);
  };

  const skins = Object.values(SKIN_PRESETS);

  return (
    <ThemeContext.Provider value={{ skinId, skin, mode, setSkinId, setMode, isDark, skins }}>
      {children}
    </ThemeContext.Provider>
  );
};
