# Template selected by the CI Agent when the Classification Agent reports
# language=java. Expects the Spring Boot fat jar already built into target/
# by the build phase (all phases share the runner workspace).
# A Dockerfile at the service repo root overrides this template.
FROM eclipse-temurin:17-jre-jammy
WORKDIR /app
COPY target/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
