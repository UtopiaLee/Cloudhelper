import { Provider } from "./api";

export interface RegionPreset {
  label: string;
  value: string;
  zones?: string[];      // GCP 必须有 zone；AWS 可空
  free_tier?: boolean;   // 是否在 Always Free 范围
}

export interface ImagePreset {
  label: string;
  value: string;     // AMI ID / GCP image URI / etc.
  ssh_user: string;
  arch?: string;     // x86_64 / arm64
}

export const REGIONS: Record<Provider, RegionPreset[]> = {
  aws: [
    { label: "us-east-1 弗吉尼亚", value: "us-east-1" },
    { label: "us-east-2 俄亥俄", value: "us-east-2" },
    { label: "us-west-2 俄勒冈", value: "us-west-2" },
    { label: "ap-northeast-1 东京", value: "ap-northeast-1" },
    { label: "ap-southeast-1 新加坡", value: "ap-southeast-1" },
    { label: "ap-southeast-2 悉尼", value: "ap-southeast-2" },
    { label: "ap-south-1 孟买", value: "ap-south-1" },
    { label: "eu-west-1 爱尔兰", value: "eu-west-1" },
    { label: "eu-central-1 法兰克福", value: "eu-central-1" },
  ],
  gcp: [
    { label: "us-west1 Oregon ★ Always Free", value: "us-west1", free_tier: true,
      zones: ["us-west1-a", "us-west1-b", "us-west1-c"] },
    { label: "us-central1 Iowa ★ Always Free", value: "us-central1", free_tier: true,
      zones: ["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"] },
    { label: "us-east1 South Carolina ★ Always Free", value: "us-east1", free_tier: true,
      zones: ["us-east1-b", "us-east1-c", "us-east1-d"] },
    { label: "asia-east1 台湾", value: "asia-east1", zones: ["asia-east1-a", "asia-east1-b", "asia-east1-c"] },
    { label: "asia-northeast1 东京", value: "asia-northeast1", zones: ["asia-northeast1-a", "asia-northeast1-b", "asia-northeast1-c"] },
    { label: "asia-southeast1 新加坡", value: "asia-southeast1", zones: ["asia-southeast1-a", "asia-southeast1-b", "asia-southeast1-c"] },
    { label: "europe-west1 比利时", value: "europe-west1", zones: ["europe-west1-b", "europe-west1-c", "europe-west1-d"] },
  ],
  oracle: [
    { label: "ap-tokyo-1 东京", value: "ap-tokyo-1" },
    { label: "ap-seoul-1 首尔", value: "ap-seoul-1" },
    { label: "ap-osaka-1 大阪", value: "ap-osaka-1" },
    { label: "ap-singapore-1 新加坡", value: "ap-singapore-1" },
    { label: "us-ashburn-1 弗吉尼亚", value: "us-ashburn-1" },
    { label: "us-phoenix-1 凤凰城", value: "us-phoenix-1" },
  ],
  azure: [
    { label: "eastus 弗吉尼亚", value: "eastus" },
    { label: "eastus2 弗吉尼亚 2", value: "eastus2" },
    { label: "westus2 华盛顿", value: "westus2" },
    { label: "westus3 凤凰城", value: "westus3" },
    { label: "centralus 爱荷华", value: "centralus" },
    { label: "northeurope 都柏林", value: "northeurope" },
    { label: "westeurope 荷兰", value: "westeurope" },
    { label: "uksouth 伦敦", value: "uksouth" },
    { label: "francecentral 巴黎", value: "francecentral" },
    { label: "germanywestcentral 法兰克福", value: "germanywestcentral" },
    { label: "swedencentral 斯德哥尔摩", value: "swedencentral" },
    { label: "switzerlandnorth 苏黎世", value: "switzerlandnorth" },
    { label: "japaneast 东京", value: "japaneast" },
    { label: "japanwest 大阪", value: "japanwest" },
    { label: "koreacentral 首尔", value: "koreacentral" },
    { label: "southeastasia 新加坡", value: "southeastasia" },
    { label: "eastasia 香港", value: "eastasia" },
    { label: "australiaeast 悉尼", value: "australiaeast" },
    { label: "centralindia 浦那", value: "centralindia" },
    { label: "uaenorth 迪拜", value: "uaenorth" },
    { label: "brazilsouth 圣保罗", value: "brazilsouth" },
    { label: "canadacentral 多伦多", value: "canadacentral" },
    { label: "southafricanorth 约翰内斯堡", value: "southafricanorth" },
  ],
};

// AWS 用别名（后端通过 SSM Parameter Store 自动解析为该 region 的最新 AMI，免费）
// 适用于所有公开 AWS region
export const AWS_IMAGE_ALIASES: ImagePreset[] = [
  { label: "Amazon Linux 2023 (x86_64)", value: "al2023", ssh_user: "ec2-user" },
  { label: "Amazon Linux 2023 (ARM64)", value: "al2023-arm64", ssh_user: "ec2-user", arch: "arm64" },
  { label: "Ubuntu 24.04 LTS (x86_64)", value: "ubuntu-24.04", ssh_user: "ubuntu" },
  { label: "Ubuntu 24.04 LTS (ARM64)", value: "ubuntu-24.04-arm64", ssh_user: "ubuntu", arch: "arm64" },
  { label: "Ubuntu 22.04 LTS (x86_64)", value: "ubuntu-22.04", ssh_user: "ubuntu" },
  { label: "Ubuntu 22.04 LTS (ARM64)", value: "ubuntu-22.04-arm64", ssh_user: "ubuntu", arch: "arm64" },
  { label: "Debian 12 (x86_64)", value: "debian-12", ssh_user: "admin" },
  { label: "Debian 12 (ARM64)", value: "debian-12-arm64", ssh_user: "admin", arch: "arm64" },
];

// 旧的按 region 写死 AMI 的字典已废弃，保留空对象避免破坏其他引用
export const AWS_IMAGES: Record<string, ImagePreset[]> = {};

// 磁盘类型（按 provider）
export interface DiskTypeOption {
  label: string;
  value: string;
  hint?: string;
}

export const DISK_TYPES: Record<Provider, DiskTypeOption[]> = {
  aws: [
    { label: "gp3 (推荐 / 通用 SSD)", value: "gp3", hint: "性价比最高，免费额度内" },
    { label: "gp2 (老款通用 SSD)", value: "gp2" },
    { label: "io2 (高 IOPS, 贵)", value: "io2", hint: "数据库等高 IO 场景" },
    { label: "st1 (吞吐优化 HDD)", value: "st1", hint: "大数据/日志，便宜" },
    { label: "sc1 (冷 HDD)", value: "sc1", hint: "归档，最便宜" },
  ],
  gcp: [
    { label: "pd-balanced (推荐)", value: "pd-balanced", hint: "性价比最优" },
    { label: "pd-standard (HDD)", value: "pd-standard", hint: "便宜，慢" },
    { label: "pd-ssd (高性能)", value: "pd-ssd", hint: "贵但快" },
    { label: "hyperdisk-balanced", value: "hyperdisk-balanced", hint: "新一代，部分实例支持" },
  ],
  oracle: [
    { label: "Block Volume (默认)", value: "" },
  ],
  azure: [
    { label: "Premium SSD", value: "Premium_LRS" },
    { label: "Standard SSD", value: "StandardSSD_LRS" },
    { label: "Standard HDD", value: "Standard_LRS" },
  ],
};

// 磁盘大小常用预设（GB）
export const DISK_SIZE_PRESETS = [10, 20, 30, 50, 100, 200];

// 各家云的默认 "免费出站流量额度" (GB/月)
// 注意：到特定国家（如中国大陆/澳大利亚）的出站可能不在免费额度内
export const DEFAULT_TRAFFIC_GB: Record<Provider, number> = {
  aws: 100,        // Free Tier 12 个月：100 GB/月（过期后只 1 GB）
  gcp: 200,        // Always Free e2-micro: 200 GB/月（北美→其他地区）
  oracle: 10240,   // Always Free: 10 TB/月
  azure: 15,       // Free 12 个月：15 GB/月
};

// GCP image 用 family 名，永远拉最新
export const GCP_IMAGES: ImagePreset[] = [
  { label: "Debian 12", value: "projects/debian-cloud/global/images/family/debian-12", ssh_user: "" },
  { label: "Ubuntu 22.04 LTS", value: "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts", ssh_user: "ubuntu" },
  { label: "Ubuntu 24.04 LTS", value: "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64", ssh_user: "ubuntu" },
  { label: "Rocky Linux 9", value: "projects/rocky-linux-cloud/global/images/family/rocky-linux-9", ssh_user: "rocky" },
  { label: "CentOS Stream 9", value: "projects/centos-cloud/global/images/family/centos-stream-9", ssh_user: "" },
];

export const ORACLE_IMAGES: ImagePreset[] = [
  { label: "Oracle Linux 8（别名）", value: "oracle-8", ssh_user: "opc" },
  { label: "Oracle Linux 9（别名）", value: "oracle-9", ssh_user: "opc" },
  { label: "Ubuntu 22.04 LTS（别名）", value: "ubuntu-22.04", ssh_user: "ubuntu" },
  { label: "Ubuntu 24.04 LTS（别名）", value: "ubuntu-24.04", ssh_user: "ubuntu" },
];

export const AZURE_IMAGES: ImagePreset[] = [
  { label: "Ubuntu 22.04 LTS（别名）", value: "ubuntu-22.04", ssh_user: "azureuser" },
  { label: "Ubuntu 24.04 LTS（别名）", value: "ubuntu-24.04", ssh_user: "azureuser" },
  { label: "Debian 12（别名）", value: "debian-12", ssh_user: "azureuser" },
  { label: "Rocky Linux 9（别名）", value: "rocky-9", ssh_user: "azureuser" },
];

// 实例规格预设
export const INSTANCE_TYPES: Record<Provider, { label: string; value: string; free_tier?: boolean }[]> = {
  aws: [
    { label: "t2.micro ★ Free Tier 12个月 (1 vCPU / 1 GB)", value: "t2.micro", free_tier: true },
    { label: "t3.micro ★ Free Tier 12个月 (2 vCPU / 1 GB)", value: "t3.micro", free_tier: true },
    { label: "t3.small (2 vCPU / 2 GB)", value: "t3.small" },
    { label: "t3.medium (2 vCPU / 4 GB)", value: "t3.medium" },
    { label: "t4g.small ARM (2 vCPU / 2 GB)", value: "t4g.small" },
  ],
  gcp: [
    { label: "e2-micro ★ Always Free 永久 (0.25-2 vCPU / 1 GB)", value: "e2-micro", free_tier: true },
    { label: "e2-small (0.5-2 vCPU / 2 GB)", value: "e2-small" },
    { label: "e2-medium (1-2 vCPU / 4 GB)", value: "e2-medium" },
    { label: "e2-standard-2 (2 vCPU / 8 GB)", value: "e2-standard-2" },
  ],
  oracle: [
    { label: "VM.Standard.A1.Flex ★ Always Free ARM (4 vCPU / 24 GB)", value: "VM.Standard.A1.Flex", free_tier: true },
    { label: "VM.Standard.E2.1.Micro ★ Always Free (1 vCPU / 1 GB)", value: "VM.Standard.E2.1.Micro", free_tier: true },
  ],
  azure: [
    { label: "Standard_B1s ★ Free 12个月 (1 vCPU / 1 GB)", value: "Standard_B1s", free_tier: true },
  ],
};
