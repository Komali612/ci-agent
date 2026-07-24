# Template selected by the CI Agent when the Classification Agent reports
# language=dotnet. Multi-stage: publishes inside the build stage (convention:
# a single app project under src/*/*.csproj), then copies the output into the
# slim ASP.NET runtime image. A Dockerfile at the service repo root overrides
# this template.
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY . .
RUN dotnet publish "$(ls src/*/*.csproj | head -1)" -c Release -o /out

FROM mcr.microsoft.com/dotnet/aspnet:8.0
WORKDIR /app
COPY --from=build /out .
EXPOSE 8080
# The app dll is the one with a matching .runtimeconfig.json.
ENTRYPOINT ["/bin/sh", "-c", "dotnet \"$(basename \"$(ls *.runtimeconfig.json | head -1)\" .runtimeconfig.json).dll\""]
