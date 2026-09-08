import { BriefcaseBusiness, ChevronDown, Compass, Moon, Sun } from "lucide-react";
import type { ProjectSummary } from "../types";

export type DeskView = "project" | "research" | "review" | "insights";

interface TopbarProps {
  projects: ProjectSummary[];
  projectId: string;
  theme: "light" | "dark";
  demoMode: boolean;
  view: DeskView;
  onProjectChange: (projectId: string) => void;
  onThemeToggle: () => void;
  onViewChange: (view: DeskView) => void;
}

export function Topbar({
  projects,
  projectId,
  theme,
  demoMode,
  view,
  onProjectChange,
  onThemeToggle,
  onViewChange,
}: TopbarProps) {
  return (
    <header className="topbar">
      <button className="brand" type="button" onClick={() => onViewChange("project")} aria-label="Project Desk home">
        <span className="brand-mark"><Compass aria-hidden="true" /></span>
        <span>Project Desk</span>
      </button>

      <nav className="desk-nav" aria-label="Workspace">
        {([
          ["project", "Project"],
          ["research", "Research"],
          ["review", "Content & review"],
          ["insights", "Insights"],
        ] as const).map(([item, label]) => (
          <button
            key={item}
            className={view === item ? "desk-nav-item active" : "desk-nav-item"}
            type="button"
            aria-current={view === item ? "page" : undefined}
            onClick={() => onViewChange(item)}
          >
            {label}
          </button>
        ))}
      </nav>

      <label className="project-switcher">
        <BriefcaseBusiness aria-hidden="true" />
        <span className="sr-only">Project</span>
        <select value={projectId} onChange={(event) => onProjectChange(event.target.value)} aria-label="Select project">
          {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
        <ChevronDown aria-hidden="true" />
      </label>

      {demoMode && <span className="demo-indicator">Demo data</span>}

      <div className="topbar-tools">
        <button
          className="icon-button theme-button"
          type="button"
          onClick={onThemeToggle}
          aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
        >
          {theme === "light" ? <Moon aria-hidden="true" /> : <Sun aria-hidden="true" />}
        </button>
      </div>
    </header>
  );
}
