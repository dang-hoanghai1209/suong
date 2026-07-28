import { NavLink } from "react-router-dom";

export function PrimaryNavigation() {
  return (
    <nav aria-label="Primary navigation" className="primary-nav">
      <NavLink to="/production">Runs</NavLink>
      <NavLink to="/production/new">Create video</NavLink>
    </nav>
  );
}
