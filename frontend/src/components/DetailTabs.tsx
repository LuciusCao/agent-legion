import { useDetailStore } from "../stores/detailStore";
import type { DetailTab, ContentType } from "../types";

const TABS: { key: DetailTab; label: string; types: ContentType[] }[] = [
  { key: "nodes", label: "交互节点", types: ["knowledge"] },
  { key: "subtitles", label: "字幕", types: ["knowledge", "question"] },
  { key: "chapters", label: "章节", types: ["knowledge", "question"] },
  { key: "metadata", label: "元数据", types: ["knowledge", "question"] },
  { key: "review", label: "审查", types: ["knowledge"] },
];

export function DetailTabs({ contentType }: { contentType: ContentType }) {
  const { activeTab, setActiveTab } = useDetailStore();
  const visibleTabs = TABS.filter((t) => t.types.includes(contentType));
  const activeIndex = visibleTabs.findIndex((t) => t.key === activeTab);

  return (
    <md-tabs
      active-tab-index={activeIndex >= 0 ? activeIndex : 0}
      onChange={(e: React.FormEvent<HTMLElement>) => {
        const idx = (e.currentTarget as any).activeTabIndex;
        const tab = visibleTabs[idx];
        if (tab) setActiveTab(tab.key);
      }}
    >
      {visibleTabs.map((tab) => (
        <md-primary-tab key={tab.key}>{tab.label}</md-primary-tab>
      ))}
    </md-tabs>
  );
}
