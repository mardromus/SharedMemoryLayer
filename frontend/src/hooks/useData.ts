import { useQuery } from "@tanstack/react-query";
import { api } from "@/services/api";
import type { Agent, Project, Role, Team } from "@/types";

export const useTeams = () => useQuery({ queryKey: ["teams"], queryFn: () => api.get<Team[]>("/api/teams") });
export const useAgents = () => useQuery({ queryKey: ["agents"], queryFn: () => api.get<Agent[]>("/api/agents") });
export const useProjects = () => useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/api/projects") });
export const useRoles = () => useQuery({ queryKey: ["roles"], queryFn: () => api.get<Role[]>("/api/roles") });
