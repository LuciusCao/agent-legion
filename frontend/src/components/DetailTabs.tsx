import { useEffect, useRef } from "react";
import { useDetailStore } from "../stores/detailStore";
import type { DetailTab, ContentType } from "../types";

const TABS: { key: DetailTab; label: string; types: ContentType[] }[] = [
  { key: "subtitles", label: "字幕", types: ["knowledge", "question"] },
  { key: "nodes", label: "交互节点", types: ["knowledge"] },
  { key: "metadata", label: "元数据", types: ["knowledge", "question"] },
];

export function DetailTabs({ contentType }: { contentType: ContentType }) {
  const { activeTab, setActiveTab } = useDetailStore();
  const tabsRef = useRef<(HTMLElement & { activeTabIndex: number }) | null>(null);
  const visibleTabs = TABS.filter((t) => t.types.includes(contentType));
  const activeIndex = visibleTabs.findIndex((t) => t.key === activeTab);
  const selectedIndex = activeIndex >= 0 ? activeIndex : 0;

  useEffect(() => {
    if (tabsRef.current) {
      tabsRef.current.activeTabIndex = selectedIndex;
    }
  }, [selectedIndex]);

  useEffect(() => {
    const tabs = tabsRef.current;
    if (!tabs) return;

    const handleChange = () => {
      const tab = visibleTabs[tabs.activeTabIndex];
      if (tab) setActiveTab(tab.key);
    };

    tabs.addEventListener("change", handleChange);
    return () => tabs.removeEventListener("change", handleChange);
  }, [setActiveTab, visibleTabs]);

  return (
    <md-tabs
      ref={tabsRef}
      active-tab-index={selectedIndex}
    >
      {visibleTabs.map((tab) => (
        <md-primary-tab key={tab.key}>{tab.label}</md-primary-tab>
      ))}
    </md-tabs>
  );
}
