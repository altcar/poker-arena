import React from "react";
import { Heading } from "@astryxdesign/core/Heading";
import { Text } from "@astryxdesign/core/Text";
import { VStack } from "@astryxdesign/core/VStack";
import { HStack } from "@astryxdesign/core/HStack";
import { Card } from "@astryxdesign/core/Card";
import { Section } from "@astryxdesign/core/Section";
import { Button } from "@astryxdesign/core/Button";
import { Badge } from "@astryxdesign/core/Badge";
import { Markdown } from "@astryxdesign/core/Markdown";
import { money, formatDate } from "../api.js";

export function PageHeader({ eyebrow, title, description, actions, level = 1 }) {
  return (
    <VStack gap={3} paddingBlockEnd={2}>
      {eyebrow ? (
        <Text type="label" color="accent" weight="semibold">
          {eyebrow}
        </Text>
      ) : null}
      <Heading level={level} type="display-3">
        {title}
      </Heading>
      {description ? (
        <Text type="large" color="secondary" as="p" style={{ maxWidth: "62ch" }}>
          {description}
        </Text>
      ) : null}
      {actions ? <HStack gap={2} wrap="wrap">{actions}</HStack> : null}
    </VStack>
  );
}

export function SectionHeading({ title, description, action, level = 2 }) {
  return (
    <HStack gap={4} hAlign="between" vAlign="end" wrap="wrap" paddingBlockEnd={2}>
      <VStack gap={1}>
        <Heading level={level}>{title}</Heading>
        {description ? (
          <Text type="body" color="secondary" as="p">
            {description}
          </Text>
        ) : null}
      </VStack>
      {action}
    </HStack>
  );
}

export function Stat({ label, value, note, tone }) {
  return (
    <Card variant="default" padding={4} elevation="low">
      <VStack gap={1}>
        <Text type="label" color="secondary">
          {label}
        </Text>
        <Heading level={3} type="display-3" color={tone === "accent" ? "accent" : "primary"}>
          {value}
        </Heading>
        {note ? (
          <Text type="supporting" color="secondary">
            {note}
          </Text>
        ) : null}
      </VStack>
    </Card>
  );
}

export function Status({ children, variant = "neutral" }) {
  return <Badge variant={variant} label={children} />;
}

export function ErrorBanner({ error }) {
  if (!error) return null;
  return (
    <Card variant="red" padding={4}>
      <Text type="body">{String(error.message || error)}</Text>
    </Card>
  );
}

export function Loading({ label = "Loading…" }) {
  return (
    <Card variant="muted" padding={6}>
      <Text type="body" color="secondary">
        {label}
      </Text>
    </Card>
  );
}

export function Empty({ title, description, action }) {
  return (
    <Card variant="muted" padding={8}>
      <VStack gap={2} hAlign="center">
        <Heading level={3}>{title}</Heading>
        {description ? (
          <Text type="body" color="secondary" justify="center">
            {description}
          </Text>
        ) : null}
        {action}
      </VStack>
    </Card>
  );
}

export function Prose({ children, contentWidth = 760 }) {
  return <Markdown contentWidth={contentWidth} headingLevelStart={2}>{children}</Markdown>;
}

export function Price({ amount, currency = "USD", period }) {
  return (
    <HStack gap={1} vAlign="end">
      <Heading level={2} type="display-2">
        {money(amount, currency)}
      </Heading>
      {period ? (
        <Text type="body" color="secondary" style={{ paddingBottom: "0.35rem" }}>
          {period}
        </Text>
      ) : null}
    </HStack>
  );
}

export function DateText({ value }) {
  return (
    <Text type="supporting" color="secondary">
      {formatDate(value)}
    </Text>
  );
}
