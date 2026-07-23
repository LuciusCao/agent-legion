import { fetchJobArtifact } from '../api'

/**
 * Fetches a job artifact and parses its content as JSON.
 * Throws the fetch error or the JSON.parse error to the caller.
 */
export async function fetchJobArtifactJson<T = unknown>(
  jobId: string,
  artifactName: string
): Promise<T> {
  const artifact = await fetchJobArtifact(jobId, artifactName)
  return JSON.parse(artifact.content) as T
}
