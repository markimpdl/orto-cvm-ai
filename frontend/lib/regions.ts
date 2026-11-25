import { REGION_LABEL } from "@/lib/api";

/** ISO-2 codes offered in the country picker (the collectors take any ISO-2; this is the short list). Brasil and
 * "Mundo todo" have their own checkboxes, so they are not in it. */
export const COUNTRIES: [string, string][] = [
  ["AE", "Emirados Árabes"], ["AR", "Argentina"], ["AU", "Austrália"], ["CA", "Canadá"], ["CL", "Chile"], ["CO", "Colômbia"],
  ["DE", "Alemanha"], ["EG", "Egito"], ["ES", "Espanha"], ["FR", "França"], ["GB", "Reino Unido"], ["IN", "Índia"],
  ["IT", "Itália"], ["JP", "Japão"], ["MX", "México"], ["PT", "Portugal"], ["SA", "Arábia Saudita"], ["US", "Estados Unidos"],
];

const COUNTRY_NAME: Record<string, string> = Object.fromEntries(COUNTRIES);

/** Name of a trend region (pt-BR source string, pass it through t()). "US" reads "Global" only on machines that
 * do not also collect the worldwide feed, so "Mundo todo" and "Estados Unidos" never look alike. */
export function regionName(code: string, chosen: string[] = []): string {
  if (code === "GLOBAL") return "Mundo todo";
  if (code === "US" && chosen.includes("GLOBAL")) return "Estados Unidos";
  return REGION_LABEL[code] ?? COUNTRY_NAME[code] ?? code;
}
