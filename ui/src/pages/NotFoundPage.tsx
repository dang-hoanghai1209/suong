import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="page-stack not-found">
      <p className="eyebrow">404</p>
      <h1>Page not found</h1>
      <p>The requested production workspace route does not exist.</p>
      <Link to="/production">Return to production dashboard</Link>
    </div>
  );
}
