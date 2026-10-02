import { Navigate, useSearchParams } from "react-router-dom";
import { useRunSession } from "../lib/runSession";
import { workspacePath } from "../lib/workspaces";

/** 시작 화면은 검색. 예전 /run 링크만 저장된 작업으로 연결한다. */
export default function WorkspaceEntry({ resume = false }: { resume?: boolean }) {
  const { jobKind } = useRunSession();
  const [params] = useSearchParams();
  const query = params.toString();
  return <Navigate to={`${workspacePath(resume ? jobKind : "similarity_search")}${query ? `?${query}` : ""}`} replace />;
}
