import React, { createContext, useContext, useEffect, useState, ReactNode } from 'react';

interface ThemeContextType {
  themeColor: string;
  mode: 'light' | 'dark' | 'system';
  setThemeColor: (color: string) => void;
  setMode: (mode: 'light' | 'dark' | 'system') => void;
  isDark: boolean;
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
  const [themeColor, setThemeColorState] = useState('#3b82f6');
  const [mode, setModeState] = useState<'light' | 'dark' | 'system'>('light');

  useEffect(() => {
    const savedColor = localStorage.getItem('themeColor');
    const savedMode = localStorage.getItem('themeMode');
    if (savedColor) setThemeColorState(savedColor);
    if (savedMode) setModeState(savedMode as 'light' | 'dark' | 'system');
  }, []);

  const isDark = mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--theme-color', themeColor);
    
    if (isDark) {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
  }, [themeColor, isDark]);

  const setThemeColor = (color: string) => {
    setThemeColorState(color);
    localStorage.setItem('themeColor', color);
  };

  const setMode = (newMode: 'light' | 'dark' | 'system') => {
    setModeState(newMode);
    localStorage.setItem('themeMode', newMode);
  };

  return (
    <ThemeContext.Provider value={{ themeColor, mode, setThemeColor, setMode, isDark }}>
      {children}
    </ThemeContext.Provider>
  );
};
